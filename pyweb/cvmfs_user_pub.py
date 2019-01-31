# Dispatch a pub-abi request
# URLs are of the form /pubapi/<request>?cid=xxxxx
#  where <request> is one of
#     exists :  returns in the body PRESENT if cid xxxxx is already
#               published in any repository or is queued for publish
#               on this server, otherwise MISSING
#     publish : queues tarball in POSTed body for publication and
#               returns OK.
# cid is the Code IDentifier, expected to be a secure hash of the tarball
#  but can be anything that is unique per tarball.

import os, threading, time
import urlparse, urllib

confcachetime = 300  # 5 minutes
userpubconffile = '/etc/cvmfs-user-pub.conf'
alloweddnsfile = '/etc/grid-security/grid-mapfile'
queuedir = '/tmp/cvmfs-user-pub'
conf = {}
alloweddns = set()
confupdatetime = 0
userpubconfmodtime = 0
alloweddnsmodtime = 0
conflock = threading.Lock()

def logmsg(ip, id, msg):
    print '(' + ip + ' ' + id + ') '+ msg

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

        if 'queuedir' in newconf:
            queuedir = newconf['queuedir'][0]

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

def dispatch(environ, start_response):
    if 'REMOTE_ADDR' not in environ:
        logmsg('-', '-', 'No REMOTE_ADDR')
        return bad_request(start_response, 'wpad-dispatch', '-', 'REMOTE_ADDR not set')
    ip = environ['REMOTE_ADDR']

    now = int(time.time())
    global userpubconf
    global alloweddns
    global confupdatetime
    conflock.acquire()
    if (now - confupdatetime) > confcachetime:
        confupdatetime = now
        conflock.release()
        newconf = parse_conf()
        newdns = parse_alloweddns()
        conflock.acquire()
        userpubconf = newconf
        alloweddns = newdns
    conf = userpubconf
    dns = alloweddns
    conflock.release()

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

    if 'PATH_INFO' not in environ:
        return bad_request(start_response, ip, cn, 'No PATH_INFO')
    pathinfo = environ['PATH_INFO']

    cid = ''
    if 'QUERY_STRING' in environ:
        parameters = urlparse.parse_qs(urllib.unquote(environ['QUERY_STRING']))
        if 'cid' in parameters:
            cid = os.path.normpath(parameters['cid'][0])
            if cid[0] == '.':
              return bad_request(start_response, ip, cn, 'cid may not start with "."')

    if 'prefix' in conf:
        prefix = conf['prefix'][0]
    else:
        prefix = 'sw'

    if pathinfo == '/exists':
        if cid == '':
            return bad_request(start_response, ip, cn, 'exists with no cid')
        if 'hostrepo' in conf:
            for hostrepo in conf['hostrepo']:
                repo = hostrepo[hostrepo.find(':')+1:]
                if os.path.exists('/cvmfs2/' + repo + '/' + prefix + '/' + cid):
                    logmsg(ip, cn, 'present in ' + repo + ': ' + cid)
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
        except Exception, e:
            logmsg(ip, cn, 'error getting publish data: ' + str(e))
            return bad_request(start_response, ip, cn, 'error getting publish data')
        return good_request(start_response, 'OK\n')

    logmsg(ip, cn, 'Unrecognized api ' + pathinfo)
    return error_request(start_response, '404 Not found', 'Unrecognized api')
