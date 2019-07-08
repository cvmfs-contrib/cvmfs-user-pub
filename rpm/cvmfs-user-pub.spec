Summary: CVMFS user publication service
Name: cvmfs-user-pub
Version: 1.4
Release: 2%{?dist}
BuildArch: noarch
Group: Applications/System
License: BSD
BuildRoot: %{_tmppath}/%{name}-%{version}-%{release}-root-%(%{__id_u} -n)
Source0: https://github.com/DrDaveD/%{name}/releases/download/%{version}/%{name}-%{version}.tar.gz

Requires: httpd
Requires: mod_wsgi
Requires: mod_ssl
# 2.5.1 is needed for the updated geoip DB for add-replica
Requires: cvmfs-server >= 2.5.1
Requires: cvmfs

%description
Accepts tarballs from authenticated users and publishes them in a cvmfs
repository.  Automatically cleans them out after maxdays of no use.

%prep
%setup -q

%install
mkdir -p $RPM_BUILD_ROOT/etc/cron.d
mkdir -p $RPM_BUILD_ROOT/etc/init.d
mkdir -p $RPM_BUILD_ROOT/etc/logrotate.d
mkdir -p $RPM_BUILD_ROOT/etc/cvmfs/default.d
mkdir -p $RPM_BUILD_ROOT/etc/httpd/conf.d
install -p -m 644 etc/%{name}.conf $RPM_BUILD_ROOT/etc/%{name}.conf
install -p -m 644 etc/%{name}.cron $RPM_BUILD_ROOT/etc/cron.d/%{name}
install -p -m 755 etc/%{name}.init $RPM_BUILD_ROOT/etc/init.d/%{name}
install -p -m 644 etc/%{name}.logrotate $RPM_BUILD_ROOT/etc/logrotate.d/%{name}
install -p -m 444 etc/%{name}.default $RPM_BUILD_ROOT/etc/cvmfs/default.d/99-%{name}.conf
install -p -m 444 misc/%{name}.conf $RPM_BUILD_ROOT/etc/httpd/conf.d/10-%{name}.conf
mkdir -p $RPM_BUILD_ROOT/usr/lib/systemd/system/httpd.service.d
install -p -m 444 misc/%{name}.service $RPM_BUILD_ROOT/usr/lib/systemd/system/%{name}.service
install -p -m 444 misc/systemd-httpd.conf $RPM_BUILD_ROOT/usr/lib/systemd/system/httpd.service.d/%{name}.conf
mkdir -p $RPM_BUILD_ROOT/var/www/wsgi-scripts/%{name}
install -p -m 555 misc/dispatch.wsgi $RPM_BUILD_ROOT/var/www/wsgi-scripts/%{name}
mkdir -p $RPM_BUILD_ROOT/usr/share/%{name}/pyweb
install -p -m 444 pyweb/* $RPM_BUILD_ROOT/usr/share/%{name}/pyweb
mkdir -p $RPM_BUILD_ROOT/usr/libexec/%{name}
install -p -m 555 libexec/gcsnapshots $RPM_BUILD_ROOT/usr/libexec/%{name}/gcsnapshots
install -p -m 555 libexec/initrepos $RPM_BUILD_ROOT/usr/libexec/%{name}/initrepos
install -p -m 555 libexec/publish $RPM_BUILD_ROOT/usr/libexec/%{name}/publish
install -p -m 555 libexec/snapshots $RPM_BUILD_ROOT/usr/libexec/%{name}/snapshots

%post
if ! getent group cvmfspub >/dev/null 2>&1 ; then
    if ! groupadd -r cvmfspub; then
        echo "ERROR: failed to groupadd cvmfspub" >&2
        exit 1
    fi
fi
if ! getent passwd cvmfspub >/dev/null 2>&1 ; then
    if ! useradd -r -g cvmfspub cvmfspub; then
        echo "ERROR: failed to useradd cvmfspub" >&2
        exit 1
    fi
fi
if [ ! -d /home/cvmfspub ]; then
    mkdir -p /home/cvmfspub
    chown cvmfspub:cvmfspub /home/cvmfspub
    chmod 755 /home/cvmfspub
fi

systemctl daemon-reload

if ! grep ^OPENSSL_ALLOW_PROXY_CERTS=1 /etc/sysconfig/httpd >/dev/null 2>&1; then
    (echo
    echo '# added by cvmfs-user-pub post install'
    echo 'OPENSSL_ALLOW_PROXY_CERTS=1') >>/etc/sysconfig/httpd
fi

for service in cvmfs-user-pub httpd; do
    if ! systemctl is-enabled --quiet $service; then
        systemctl enable $service
    fi
    if ! systemctl is-active --quiet $service; then
        systemctl start $service
    elif [ $service = httpd ]; then
        systemctl reload $service
    fi
done

/usr/libexec/%{name}/initrepos

%files
%config(noreplace) /etc/%{name}.conf
/etc/cron.d/*
/etc/init.d/*
/etc/logrotate.d/*
/etc/cvmfs/default.d/*
/etc/httpd/conf.d/*
/var/www/wsgi-scripts/%{name}
/usr/share/%{name}
/usr/libexec/%{name}
/usr/lib/systemd/system/%{name}.service
/usr/lib/systemd/system/httpd.service.d


%changelog
* Mon Jul 08 2019 Dave Dykstra <dwd@fnal.gov> 1.4-2
- Add COPYING with the Fermitools license to the source, refer to it
  in source files.

* Mon Jul 08 2019 Dave Dykstra <dwd@fnal.gov> 1.4-1
- Fix crash at startup due to missing userpubconf global variable

* Wed Jun 26 2019 Dave Dykstra <dwd@fnal.gov> 1.3-1
- Do cvmfs_server mount -a and clean out reflogs at boot time
- Make client certificate optional
- Add ping api

* Tue Jun 18 2019 Dave Dykstra <dwd@fnal.gov> 1.2-1
- Add config API to return list of repos.  http only to avoid user cert.

* Wed Jun 12 2019 Dave Dykstra <dwd@fnal.gov> 1.1-1
- Add the previously published path after 'PRESENT:' for each of the
  3 api calls (exists, update, publish) when cid is already present.
- Save the CN of the person who published a tarball (or just the userid
  if the CN ends in UID:userid) in a .publisher file in the top level
  directory of an unpacked tarball, and also as the contents in timestamp
  files.

* Tue Jun 11 2019 Dave Dykstra <dwd@fnal.gov> 1.0-1
- add updates api to touch a timestamp only if cid is present
- add automatic removal, including maxdays config option
- support cids with zero or one slashes

* Fri Jun 07 2019 Dave Dykstra <dwd@fnal.gov> 0.9-1
- make the publish api touch a timestamp file if cid already present
- change api requests from localhost to not require authentication
- remove publish file if creating it causes an error
- add support for up to 7 layers of RFC proxies

* Wed May 01 2019 Dave Dykstra <dwd@fnal.gov> 0.8-1
- support gc on snapshots
- make .cvmfsdirtab be owned by cvmfspub
- reload httpd again on rpm upgrade

* Thu Apr 25 2019 Dave Dykstra <dwd@fnal.gov> 0.7-1
- Mount all /cvmfs2 repos from localhost
- Create .cvmfsdirtab files in repos
- Remove new_repository file from repos
- Do the systemctl restart from inside initrepos instead of rpm %post
- Don't enable auto garbage collection in replicas (will add code later
  to do it from cron)
- Require cvmfs-server-2.5.1
- Disable options set in cvmfs-config-osg rpm in case it is present

* Wed Apr 24 2019 Dave Dykstra <dwd@fnal.gov> 0.6-1
- Add automatic repository initialization from config file

* Fri Apr 12 2019 Dave Dykstra <dwd@fnal.gov> 0.5-1
- Add delete API
- Switch back to using script to publish instead of tarball ingestion,
  because of a bug in ingestion

* Wed Apr 10 2019 Dave Dykstra <dwd@fnal.gov> 0.4-1
- Use cvmfs-server 2.6.0 tarball ingestion
- Add running cvmfs-server garbage collection

* Thu Feb 28 2019 Dave Dykstra <dwd@fnal.gov> 0.3-1
- Added PRESENT response to publish and pass prefix to publish script

* Thu Feb 28 2019 Dave Dykstra <dwd@fnal.gov> 0.2-1
- Switched to nobody user and group
- Added reading from /etc/cvmfs-user-pub.conf
- Added publish API

* Thu Dec 20 2018 Dave Dykstra <dwd@fnal.gov> 0.1-1
- Initial version
