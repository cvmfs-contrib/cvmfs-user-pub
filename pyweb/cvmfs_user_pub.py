# Dispatch a cvmfs-user-pub api request
# URLs are of the form /pubapi/<request>?cid=xxxxx
#  where <request> is one of
#     exists :  Returns in the body PRESENT:path if cid xxxxx is already
#               published in any repository or is queued for publish
#               on this server, otherwise returns MISSING. "path" is the
#               full path to where the cid was published.
#     update :  Exactly like "exists" except that if the cid xxxxx is
#               present, also queues a publish to update a timestamp for
#               the cid, in any repository.
#     publish : Queues tarball in POSTed body for publication in cid
#               xxxxx.  If already present, returns PRESENT:path and
#               publishes a timestamp like "update", otherwise returns OK
#               and publishing is queued to happen as soon as possible.
# All of the above are on https and require a user certificate.
#               
# cid is the Code IDentifier, expected to be a secure hash of the
# tarball but can be anything that is unique per tarball.  It may
# optionally contain a slash to group tarballs by project (but no more
# than one slash).
#
# Additional API URLs on http or https without user cert of the form
#  /pubapi/<request> where <request> is
#     config :  Returns configuration, currently the label "repos:"
#               followed by a comma-separated list of repositories
#     ping :   Returns OK in the body
#
# This source file is Copyright (c) 2019, FERMI NATIONAL ACCELERATOR
#    LABORATORY.  All rights reserved.
# For details of the Fermitools (BSD) license see COPYING.

import os, threading, time, datetime
import Queue, socket, subprocess, select
import urlparse, urllib
import shutil, re

confcachetime = 300  # 5 minutes
userpubconffile = '/etc/cvmfs-user-pub.conf'
alloweddnsfile = '/etc/grid-security/grid-mapfile'
queuedir = '/tmp/cvmfs-user-pub'
prefix = 'sw'
gcstarthour = 3
maxdays = 30
alloweddns = set()
userpubconf = {}
confupdatetime = 0
userpubconfmodtime = 0
alloweddnsmodtime = 0
conflock = threading.Lock()
pubqueue = Queue.Queue()

def logmsg(ip, id, msg):
    print '(' + ip + ' ' + id + ') '+ msg

def threadmsg(msg):
    print '(' + threading.current_thread().name + ') '+ msg

def error_request(start_response, response_code, response_body):
    response_body = response_body + '\n'
    start_response(response_code,
                   [('Cache-control', 'max-age=0'),
                    ('Content-Length', str(len(response_body)))])
    return [response_body]

def bad_request(start_response, ip, id, reason):
    response_body = 'Bad request: ' + reason
    logmsg(ip, id, 'bad request: ' + reason)
    return error_request(start_response, '400 Bad request', response_body)

def good_request(start_response, response_body):
    response_code = '200 OK'
    start_response(response_code,
                  [('Content-Type', 'text/plain'),
                   ('Cache-control', 'max-age=0'),
                   ('Content-Length', str(len(response_body)))])
    return [response_body]

def parse_conf():
    global userpubconfmodtime
    newconf = {}
    savemodtime = userpubconfmodtime
    try:
        modtime = os.stat(userpubconffile).st_mtime
        if modtime == userpubconfmodtime:
            # no change
            return userpubconf
        userpubconfmodtime = modtime
        logmsg('-', '-', 'reading ' + userpubconffile)
        for line in open(userpubconffile, 'r').read().split('\n'):
            line = line.split('#',1)[0]  # removes comments
            words = line.split(None,1)
            if len(words) < 2:
                continue
            if words[0] in newconf:
                newconf[words[0]].append(words[1])
            else:
                newconf[words[0]] = [words[1]]

    except Exception, e:
        logmsg('-', '-', 'error reading ' + userpubconffile + ', continuing: ' + str(e))
        userpubconfmodtime = savemodtime
        return userpubconf
    return newconf

def parse_alloweddns():
    global alloweddnsmodtime
    newdns = set()
    savemodtime = alloweddnsmodtime
    try:
        modtime = os.stat(alloweddnsfile).st_mtime
        if modtime == alloweddnsmodtime:
            # no change
            return alloweddns
        alloweddnsmodtime = modtime
        logmsg('-', '-', 'reading ' + alloweddnsfile)
        for line in open(alloweddnsfile, 'r').read().split('\n'):
            # take the part between double quotes
            if len(line) == 0 or line[0] == '#':
                continue
            parts = line.split('"')
            if len(parts) > 2:
                newdns.add(parts[1])
    except Exception, e:
        logmsg('-', '-', 'error reading ' + alloweddnsfile + ', continuing: ' + str(e))
        alloweddnsmodtime = savemodtime
        return alloweddns

    return newdns

# Generate all cids in the tree below path.  Cids can have zero or one
#  slashes.  Assume they have zero slashes if the first subdirectory found
#  has no .cvmfscatalog, otherwise assume they have one slash.  Cids of both
#  types may be intermixed if desired.
def findcids(path):
    if not os.path.exists(path):
        return
    for upper in os.listdir(path):
        uppath = path + '/' + upper
        if not os.path.isdir(uppath):
            continue
        hassubcatalog = False
        for lower in os.listdir(uppath):
            lowpath = uppath + '/' + lower
            if not os.path.isdir(lowpath):
                continue
            if not hassubcatalog:
                if not os.path.exists(lowpath + '/.cvmfscatalog'):
                    break
                hassubcatalog = True
            yield upper + '/' + lower
        if not hassubcatalog:
            yield upper

# Return True if the cid and its timestamps are all older than maxdays.
# Return False if not found, which can happen because there is a delay
#  between publish and the time that updates appear in /cvmfs2.
def cidexpired(cid, conf, now):
    foundone = False
    if 'hostrepo' in conf:
        for hostrepo in conf['hostrepo']:
            repo = hostrepo[hostrepo.find(':')+1:]
            for pubdir in ['ts', prefix]:
                path = '/cvmfs2/' + repo + '/' + pubdir + '/' + cid
                if os.path.exists(path):
                    foundone = True
                    seconds = os.path.getmtime(path)
                    days = int((now - seconds) / (60 * 60 * 24))
                    if (days <= maxdays):
                        return False
    return foundone

def runthreadcmd(cmd, msg):
    threadmsg(cmd)
    p = subprocess.Popen( ('/bin/bash', '-c', cmd), bufsize=1, 
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    # the following logic is from
    #  https://stackoverflow.com/questions/23677526/checking-to-see-if-there-is-more-data-to-read-from-a-file-descriptor-using-pytho
    while True:
        ready, _, _ = select.select((p.stdout, p.stderr), (), ())
        for fd in (p.stdout, p.stderr):
            if fd in ready:
                line = fd.readline()
                if line:
                    threadmsg(line)
                elif p.returncode is not None:
                    ready = filter(lambda x: x is not fd, ready)
        if p.poll() is not None and not ready:
            break

    if p.returncode != 0:
        threadmsg(msg + ' failed with code ' + str(p.returncode))
    else:
        threadmsg(msg + ' succeeded')
    return p.returncode

# do the operations on a cvmfs repository
def publishloop(repo, reponum, conf):
    threadmsg('thread ' + str(reponum) + ' started for publishing to /cvmfs/' + repo)
    gcdone = False
    while True:
        cid = None
        try:
            cid, cn, conf, option = pubqueue.get(True, 60)
        except Queue.Empty, e:
            # cid will be None in this case
            pass

        if cid is not None:
            pubdir = prefix
            if 'ts' in option:
                # This particular directory name 'ts' (for timestamp)
                #  tells the publish script to only touch the file
                pubdir = 'ts'
            # enclose cid in single quotes because it comes from the user
            cmd = "/usr/libexec/cvmfs-user-pub/publish " + repo + " " + \
                    queuedir + " " + pubdir + " '" + cid + "' '" + cn + "'"
            returncode = runthreadcmd(cmd, 'publish ' + cid)
            if 'queued' in option:
                cidpath = os.path.join(queuedir,cid)
                threadmsg('removing ' + cidpath)
                os.remove(cidpath)

        thishour = datetime.datetime.now().hour
        if thishour != (gcstarthour - 1 + reponum) % 24:
            gcdone = False
            continue
        if gcdone:
            continue

        # time for cleanup too
        threadmsg('starting cleanup in ' + repo)

        now = int(time.time())
        dirdeletelist = []
        filedeletelist = []

        # find expired cids in this repo, put on list to delete
        cidpath = '/cvmfs/' + repo + '/' + prefix
        for cid in findcids(cidpath):
            if cidexpired(cid, conf, now):
                dirdeletelist.append(cidpath + '/' + cid)

        # find ts files in this repo that have no matching cid
        tspath = '/cvmfs/' + repo + '/ts'
        for dirpath, _, files in os.walk(tspath):
            ciddir =  dirpath[len(tspath)+1:]
            if ciddir != '':
                ciddir = ciddir + '/'
            for file in files:
                cid = ciddir + file
                if cidinrepo(cid, conf) is None:
                    filedeletelist.append(tspath + '/' + cid)

        if len(dirdeletelist) > 0 or len(filedeletelist) > 0:
            threadmsg('starting transaction for deletes in ' + repo)
            cmd = "cvmfs_server transaction '" + repo + "'"
            if runthreadcmd(cmd, 'start transaction ' + repo) == 0:
                for dir in dirdeletelist:
                    threadmsg('removing ' + dir)
                    shutil.rmtree(dir)
                for file in filedeletelist:
                    threadmsg('removing ' + file)
                    os.remove(file)
                threadmsg('publishing deletes in ' + repo)
                cmd = "cvmfs_server publish '" + repo + "'"
                runthreadcmd(cmd, 'publishing deletes ' + repo)

        threadmsg('running gc on ' + repo)
        cmd = "cvmfs_server gc -f '" + repo + "'"
        runthreadcmd(cmd, 'gc ' + repo)
        gcdone = True

def cidinrepo(cid, conf):
    if 'hostrepo' in conf:
        for hostrepo in conf['hostrepo']:
            repo = hostrepo[hostrepo.find(':')+1:]
            if os.path.exists('/cvmfs2/' + repo + '/' + prefix + '/' + cid):
                return repo
    return None

def repocidpath(repo, cid):
    return '/cvmfs/' + repo + '/' + prefix + '/' + cid

def dispatch(environ, start_response):
    if 'REMOTE_ADDR' not in environ:
        logmsg('-', '-', 'No REMOTE_ADDR')
        return bad_request(start_response, 'wpad-dispatch', '-', 'REMOTE_ADDR not set')
    ip = environ['REMOTE_ADDR']

    now = int(time.time())
    conflock.acquire()
    global confupdatetime
    global userpubconf
    if (now - confupdatetime) > confcachetime:
        confupdatetime = now
        if len(userpubconf) != 0:
            # release if not reading for the first time, to
            #   let other threads continue to use the old copy
            conflock.release()
        newconf = parse_conf()
        newdns = parse_alloweddns()

        # things to do when config file has changed
        global queuedir
        if 'queuedir' in newconf:
            queuedir = newconf['queuedir'][0]

        global prefix
        if 'prefix' in newconf:
            prefix = newconf['prefix'][0]

        global gcstarthour
        if 'gcstarthour' in newconf:
            gcstarthour = int(newconf['gcstarthour'][0])

        global maxdays
        if 'maxdays' in newconf:
            maxdays = int(newconf['maxdays'][0])

        if 'hostrepo' in newconf:
            myhost = socket.gethostname()
            myshorthost = myhost.split('.')[0]
            reponum = 0
            for hostrepo in newconf['hostrepo']:
                reponum += 1
                colon = hostrepo.find(':')
                host = hostrepo[0:colon]
                repo = hostrepo[colon+1:]
                if host != myhost and host != myshorthost:
                    continue
                pubrepo = 'Pub-' + repo
                gotit = False
                for thread in threading.enumerate():
                    if thread.name == pubrepo:
                        gotit = True
                        break
                if not gotit:
                    thread = threading.Thread(name=pubrepo,
                                              target=publishloop,
                                              args=[repo, reponum, newconf])
                    thread.start()

        if len(userpubconf) != 0:
            conflock.acquire()
        userpubconf = newconf
        global alloweddns
        alloweddns = newdns
    conf = userpubconf
    dns = alloweddns
    conflock.release()

    if 'PATH_INFO' not in environ:
        return bad_request(start_response, ip, '-', 'No PATH_INFO')
    pathinfo = environ['PATH_INFO']

    parameters = {}
    if 'QUERY_STRING' in environ:
        parameters = urlparse.parse_qs(urllib.unquote(environ['QUERY_STRING']))

    if pathinfo == '/config':
        logmsg(ip, '-', 'Returning config')
        body = 'repos:'
        if 'hostrepo' in conf:
            gotone = False
            for hostrepo in conf['hostrepo']:
                if gotone:
                    body += ','
                else:
                    gotone = True
                body += hostrepo[hostrepo.find(':')+1:]
        body += '\n'
        return good_request(start_response, body)

    if pathinfo == '/ping':
        if 'hostrepo' not in conf:
            return bad_request(start_response, ip, '-', 'not configured')
        return good_request(start_response, 'OK\n')

    if 'SSL_CLIENT_S_DN' not in environ:
        if ip != '127.0.0.1':
            logmsg(ip, '-', 'No client cert, access denied')
            return error_request(start_response, '403 Access denied', 'Client cert required')
        cn = 'localhost'
    else:
        dn = environ['SSL_CLIENT_S_DN']

        cnidx = -1
        while True:
            cnidx = dn.rfind('/CN=')
            if cnidx < 0:
                logmsg(ip, '-', 'No /CN=, access denied: ' + dn)
                return error_request(start_response, '403 Access denied', 'Malformed DN')
            cnvalue = dn[cnidx+4:]
            numbers = re.findall('\d+', cnvalue)
            if len(numbers) == 1 and numbers[0] == cnvalue:
                # delete a level of proxy from the end
                dn = dn[0:cnidx]
            else:
                # not a proxy level
                break

        if dn not in dns:
            logmsg(ip, '-', 'DN unrecognized, access denied: ' + dn)
            return error_request(start_response, '403 Access denied', 'Unrecognized DN')

        uididx = dn.find('/CN=UID:')
        if uididx >= 0:
            cn = dn[uididx+8:]
        else:
            cn = dn[cnidx+4:]
        endidx = cn.find('/')
        if endidx >= 0:
            cn = cn[0:endidx]

    cid = ''
    if 'cid' in parameters:
        cid = os.path.normpath(parameters['cid'][0])
        if cid[0] == '.':
            return bad_request(start_response, ip, cn, 'cid may not start with "."')
        if ("'" in cid) or ('\\' in cid):
            # these are special to bash inside of single quotes
            return bad_request(start_response, ip, cn, 'disallowed character in cid')
        if cid.count('/') > 1:
            # without this restriction it is challenging for cleanup
            return bad_request(start_response, ip, cn, 'at most one slash allowed in cid')

    if pathinfo == '/exists':
        if cid == '':
            return bad_request(start_response, ip, cn, 'exists with no cid')
        inrepo = cidinrepo(cid, conf)
        if inrepo is not None:
            logmsg(ip, cn, 'present in ' + inrepo + ': ' + cid)
            return good_request(start_response,
                'PRESENT:' + repocidpath(inrepo, cid) + '\n')
        logmsg(ip, cn, cid + ' missing')
        return good_request(start_response, 'MISSING\n')

    if pathinfo == '/update':
        if cid == '':
            return bad_request(start_response, ip, cn, 'update with no cid')
        inrepo = cidinrepo(cid, conf)
        if inrepo is not None:
            logmsg(ip, cn, cid + ' present in ' + inrepo + ', updating')
            pubqueue.put([cid, cn, conf, 'ts'])
            return good_request(start_response,
                'PRESENT:' + repocidpath(inrepo, cid) + '\n')
        logmsg(ip, cn, cid + ' missing, skipping update')
        return good_request(start_response, 'MISSING\n')

    if pathinfo == '/publish':
        if cid == '':
            return bad_request(start_response, ip, cn, 'publish with no cid')
        if not os.path.exists(queuedir):
            os.mkdir(queuedir)
        ciddir = os.path.join(queuedir,os.path.dirname(cid))
        cidpath = os.path.join(queuedir,cid)
        try:
            if not os.path.exists(ciddir):
                os.mkdir(ciddir)
            contentlength = environ.get('CONTENT_LENGTH','0')
            length = int(contentlength)
            input = environ['wsgi.input']
            with open(cidpath, 'w') as output:
                while length > 0:
                    bufsize = 16384
                    if bufsize > length:
                        bufsize = length
                    buf = input.read(bufsize)
                    output.write(buf)
                    length -= len(buf)
            logmsg(ip, cn, 'wrote ' + contentlength + ' bytes to ' + cidpath)
        except Exception, e:
            logmsg(ip, cn, 'error getting publish data: ' + str(e))
            try:
                os.remove(cidpath)
            except OSError:
                pass
            return bad_request(start_response, ip, cn, 'error getting publish data')
        inrepo = cidinrepo(cid, conf)
        if inrepo is not None:
            logmsg(ip, cn, cid + ' already present in ' + inrepo)
            pubqueue.put([cid, cn, conf, 'ts,queued'])
            return good_request(start_response,
                'PRESENT:' + repocidpath(inrepo, cid) + '\n')
        pubqueue.put([cid, cn, conf, 'queued'])
        return good_request(start_response, 'OK\n')

    logmsg(ip, cn, 'Unrecognized api ' + pathinfo)
    return error_request(start_response, '404 Not found', 'Unrecognized api')
