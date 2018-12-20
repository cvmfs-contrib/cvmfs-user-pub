# Dispatch a pub-abi request

import os, threading, time

confcachetime = 300  # 5 minutes
gridmapfile = '/etc/grid-security/grid-mapfile'
alloweddns = set()
confupdatetime = 0
confmodtime = 0
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
    global confmodtime
    newdns = set()
    savemodtime = confmodtime
    try:
        modtime = os.stat(gridmapfile).st_mtime
        if modtime == confmodtime:
            # no change
            return alloweddns
        confmodtime = modtime
        logmsg('reading ' + gridmapfile)
        for line in open(gridmapfile, 'r').read().split('\n'):
            # take the part between double quotes
            if len(line) == 0 or line[0] == '#':
                continue
            parts = line.split('"')
            if len(parts) > 2:
                newdns.add(parts[1])
    except Exception, e:
        logmsg('error reading ' + gridmapfile + ', continuing: ' + str(e))
        confmodtime = savemodtime
        return alloweddns

    return newdns

def dispatch(environ, start_response):
    now = int(time.time())
    global alloweddns
    global confupdatetime
    conflock.acquire()
    if (now - confupdatetime) > confcachetime:
        confupdatetime = now
        conflock.release()
        newconf = parse_conf()
        conflock.acquire()
        alloweddns = newconf
    dns = alloweddns
    conflock.release()

    if 'SSL_CLIENT_S_DN' not in environ:
        return error_request(start_response, '403 Access denied', 'Client cert required')
    dn = environ['SSL_CLIENT_S_DN']
    if dn not in dns:
        return error_request(start_response, '403 Access denied', 'Unrecognized DN')

    body = 'hello ' + dn + '\n'
    return good_request(start_response, body)
