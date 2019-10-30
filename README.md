# cvmfs-user-pub
Service for publishing user code tarballs in cvmfs repositories

Pre-built rpms available in [cvmfs-contrib](https://cvmfs-contrib.github.io/).

For a description of the service, see my
[talk](https://indico.cern.ch/event/757415/contributions/3416919/attachments/1854267/3045098/CVMFSWorkshop20190603.pdf)
from the 2019 CernVM Workshop.

The API is described in the comments of
[pyweb/cvmfs_user_pub.py](pyweb/cvmfs_user_pub.py)
and the configuration is in the comments of
[etc/cvmfs-user-pub.conf](etc/cvmfs-user-pub.conf).

In order to be able to reuse tarballs where input files have not
changed, and have it use a consistent CID hash, either set `GZIP=-n` in
the environment of `tar` or pipe to `gzip -n` instead of using `tar -z`.
