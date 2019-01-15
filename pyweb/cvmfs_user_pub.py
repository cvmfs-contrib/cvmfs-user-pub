# Dispatch a pub-abi request
# URLs are of the form /pubapi/<request>?cid=xxxxx
#  where <request> is one of
#     exists :  returns in the body PRESENT if cid xxxxx is already
#               published in any repository or is queued for publish
#               on this server, otherwise MISSING
#     publish : queues tarball in POSTed body for publication and
#               returns OK
# cid is the Code IDentifier, expected to be a secure hash of the tarball
#  but can be anything that is unique per tarball.

import os, threading, time

confcachetime = 300  # 5 minutes
userpubconffile = '/etc/cvmfs-user-pub.conf'
alloweddnsfile = '/etc/grid-security/grid-mapfile'
conf = {}
alloweddns = set()
confupdatetime = 0
userpubconfmodtime = 0
alloweddnsmodtime = 0
conflock = threading.Lock()

def error_request(start_response, response_code, response_body):
    response_body = response_body + '\n'
    start_response(response_code,
                   [('Cache-control', 'max-age=0'),
                    ('Content-Length', str(len(response_body)))])
    return [response_body]

def bad_request(start_response, reason):
    response_body = 'Bad Request: ' + reason
    return error_request(start_response, '400 Bad Request', response_body)

def good_request(start_response, response_body):
    response_code = '200 OK'
    start_response(response_code,
                  [('Content-Type', 'text/plain'),
                   ('Cache-control', 'max-age=0'),
                   ('Content-Length', str(len(response_body)))])
    return [response_body]

mypid = '[' + str(os.getpid()) + ']'
def logmsg(msg):
    print '[' + mypid + '] ' + msg

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
        logmsg('reading ' + userpubconffile)
        for line in open(userpubconffile, 'r').read().split('\n'):
            line = line.split('#',1)[0]  # removes comments
            words = line.split(None,1)
            if len(words) < 2:
                continue
            newconf[words[0]] = words[1]
    except Exception, e:
        logmsg('error reading ' + userpubconffile + ', continuing: ' + str(e))
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
        logmsg('reading ' + alloweddnsfile)
        for line in open(alloweddnsfile, 'r').read().split('\n'):
            # take the part between double quotes
            if len(line) == 0 or line[0] == '#':
                continue
            parts = line.split('"')
            if len(parts) > 2:
                newdns.add(parts[1])
    except Exception, e:
        logmsg('error reading ' + alloweddnsfile + ', continuing: ' + str(e))
        alloweddnsmodtime = savemodtime
        return alloweddns

    return newdns

def dispatch(environ, start_response):
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
        logmsg('No client cert, access denied')
        return error_request(start_response, '403 Access denied', 'Client cert required')
    dn = environ['SSL_CLIENT_S_DN']
    if dn not in dns:
        logmsg('DN unrecognized, access denied: ' + dn)
        return error_request(start_response, '403 Access denied', 'Unrecognized DN')

    body = 'hello ' + dn + '\n'
    return good_request(start_response, body)
