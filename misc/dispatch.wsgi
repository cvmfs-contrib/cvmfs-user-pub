#! /usr/bin/env python2
#
# This source file is Copyright (c) 2019, FERMI NATIONAL ACCELERATOR
#    LABORATORY.  All rights reserved.
# For details of the Fermitools (BSD) license see COPYING.

import cvmfs_user_pub

def application(environ, start_response):

    return cvmfs_user_pub.dispatch(environ, start_response)
