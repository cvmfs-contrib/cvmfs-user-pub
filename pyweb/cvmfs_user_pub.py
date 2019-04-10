# Dispatch a pub-abi request
# URLs are of the form /pubapi/<request>?cid=xxxxx
#  where <request> is one of
#     exists :  returns in the body PRESENT if cid xxxxx is already
#               published in any repository or is queued for publish
#               on this server, otherwise MISSING
#     publish : queues tarball in POSTed body for publication in cid
#               xxxxx.  If already present, returns PRESENT skips
#               publish, otherwise returns OK and publishing will
#               happen as soon as possible.
#               
# cid is the Code IDentifier, expected to be a secure hash of the tarball
#  but can be anything that is unique per tarball.

import os, threading, time, datetime
import Queue, socket, subprocess, select
import urlparse, urllib

confcachetime = 300  # 5 minutes
userpubconffile = '/etc/cvmfs-user-pub.conf'
alloweddnsfile = '/etc/grid-security/grid-mapfile'
queuedir = '/tmp/cvmfs-user-pub'
prefix = 'sw'
gcstarthour = 3
conf = {}
alloweddns = set()
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
    return

# do the operations on a cvmfs repository
def publishloop(repo, reponum):
    threadmsg('thread ' + str(reponum) + ' started for publishing to /cvmfs/' + repo)
    gcdone = False
    while True:
        cid = None
        try:
            cid = pubqueue.get(True, 60)
        except Queue.Empty, e:
            # cid will be None in this case
            pass

        if cid is not None:
            threadmsg('publishing to /cvmfs/' + repo + '/' + prefix + '/' + cid)
            # enclose cid in single quotes because it comes from the user
            cmd = "zcat " + queuedir + "/'" + cid + "' | " + \
                "cvmfs_server ingest -t - " + \
                                    "-b " + prefix + "/'" + cid + "' " + repo
            returncode = runthreadcmd(cmd, 'publish ' + cid)
            cidpath = os.path.join(queuedir,cid)
            threadmsg('removing ' + cidpath)
            os.remove(cidpath)

        thishour = datetime.datetime.now().hour
        if thishour == (gcstarthour + reponum) % 24:
            if not gcdone:
                threadmsg('running gc on ' + repo)
                cmd = "cvmfs_server gc -f '" + repo + "'"
                returncode = runthreadcmd(cmd, 'gc ' + repo)
                gcdone = True
        else:
            gcdone = False

def cidinrepo(cid, conf):
    if 'hostrepo' in conf:
        for hostrepo in conf['hostrepo']:
            repo = hostrepo[hostrepo.find(':')+1:]
            if os.path.exists('/cvmfs2/' + repo + '/' + prefix + '/' + cid):
                return repo
    return None

def dispatch(environ, start_response):
    if 'REMOTE_ADDR' not in environ:
        logmsg('-', '-', 'No REMOTE_ADDR')
        return bad_request(start_response, 'wpad-dispatch', '-', 'REMOTE_ADDR not set')
    ip = environ['REMOTE_ADDR']

    now = int(time.time())
    global userpubconf
    global alloweddns
    global confupdatetime
    global queuedir
    global prefix
    global gcstarthour
    conflock.acquire()
    if (now - confupdatetime) > confcachetime:
        confupdatetime = now
        conflock.release()
        newconf = parse_conf()
        newdns = parse_alloweddns()

        # things to do when config file has changed
        if 'queuedir' in newconf:
            queuedir = newconf['queuedir'][0]

        if 'prefix' in newconf:
            prefix = newconf['prefix'][0]

        if 'gcstarthour' in newconf:
            gcstarthour = int(newconf['gcstarthour'][0])

        if 'hostrepo' in newconf:
            myhost = socket.gethostname().split('.')[0]
            reponum = 0
            for hostrepo in newconf['hostrepo']:
                colon = hostrepo.find(':')
                if hostrepo[0:colon] != myhost:
                    continue
                repo = hostrepo[colon+1:]
                pubrepo = 'Pub-' + repo
                gotit = False
                for thread in threading.enumerate():
                    if thread.name == pubrepo:
                        gotit = True
                        break
                if not gotit:
                    thread = threading.Thread(name=pubrepo,
                                              target=publishloop,
                                              args=[repo, reponum])
                    thread.start()
                reponum += 1

        conflock.acquire()
        userpubconf = newconf
        alloweddns = newdns
    conf = userpubconf
    dns = alloweddns
    conflock.release()

    if 'PATH_INFO' not in environ:
        return bad_request(start_response, ip, cn, 'No PATH_INFO')
    pathinfo = environ['PATH_INFO']

    parameters = {}
    if 'QUERY_STRING' in environ:
        parameters = urlparse.parse_qs(urllib.unquote(environ['QUERY_STRING']))

    if 'SSL_CLIENT_S_DN' not in environ:
        logmsg(ip, '-', 'No client cert, access denied')
        return error_request(start_response, '403 Access denied', 'Client cert required')
    dn = environ['SSL_CLIENT_S_DN']
    if dn not in dns:
        logmsg(ip, '-', 'DN unrecognized, access denied: ' + dn)
        return error_request(start_response, '403 Access denied', 'Unrecognized DN')

    cnidx = dn.find('/CN=')
    if cnidx < 0:
        logmsg(ip, '-', 'No /CN=, access denied: ' + dn)
        return error_request(start_response, '403 Access denied', 'Malformed DN')
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

    if pathinfo == '/exists':
        if cid == '':
            return bad_request(start_response, ip, cn, 'exists with no cid')
        inrepo = cidinrepo(cid, conf)
        if inrepo is not None:
            logmsg(ip, cn, 'present in ' + inrepo + ': ' + cid)
            return good_request(start_response, 'PRESENT\n')
        logmsg(ip, cn, 'missing: ' + cid)
        return good_request(start_response, 'MISSING\n')

    if pathinfo == '/publish':
        if cid == '':
            return bad_request(start_response, ip, cn, 'pathinfo with no cid')
        if not os.path.exists(queuedir):
            os.mkdir(queuedir)
        ciddir = os.path.join(queuedir,os.path.dirname(cid))
        try:
            if not os.path.exists(ciddir):
                os.mkdir(ciddir)
            cidpath = os.path.join(queuedir,cid)
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
            inrepo = cidinrepo(cid, conf)
            if inrepo is not None:
                logmsg(ip, cn, cid + ' already present in ' + inrepo)
                logmsg(ip, cn, 'removing ' + cidpath)
                os.remove(cidpath)
                return good_request(start_response, 'PRESENT\n')
            pubqueue.put(cid)
        except Exception, e:
            logmsg(ip, cn, 'error getting publish data: ' + str(e))
            return bad_request(start_response, ip, cn, 'error getting publish data')
        return good_request(start_response, 'OK\n')

    logmsg(ip, cn, 'Unrecognized api ' + pathinfo)
    return error_request(start_response, '404 Not found', 'Unrecognized api')
