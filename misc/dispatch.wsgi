#! /usr/bin/env python

import cvmfs_user_pub

def application(environ, start_response):

    return cvmfs_user_pub.dispatch(environ, start_response)
