Summary: CVMFS user publication service
Name: cvmfs-user-pub
Version: 0.5
Release: 1%{?dist}
BuildArch: noarch
Group: Applications/System
License: BSD
BuildRoot: %{_tmppath}/%{name}-%{version}-%{release}-root-%(%{__id_u} -n)
Source0: https://github.com/DrDaveD/%{name}/releases/download/%{version}/%{name}-%{version}.tar.gz

Requires: httpd
Requires: mod_wsgi
Requires: mod_ssl
Requires: cvmfs-server

%description
Accepts tarballs from authenticated users and publishes them in a
cvmfs repository.

%prep
%setup -q

%install
mkdir -p $RPM_BUILD_ROOT/etc/init.d
mkdir -p $RPM_BUILD_ROOT/etc/httpd/conf.d
install -p -m 644 etc/%{name}.conf $RPM_BUILD_ROOT/etc/%{name}.conf
install -p -m 755 etc/%{name}.init $RPM_BUILD_ROOT/etc/init.d/%{name}
install -p -m 444 misc/%{name}.conf $RPM_BUILD_ROOT/etc/httpd/conf.d/10-%{name}.conf
mkdir -p $RPM_BUILD_ROOT/usr/lib/systemd/system/httpd.service.d
install -p -m 444 misc/%{name}.service $RPM_BUILD_ROOT/usr/lib/systemd/system/%{name}.service
install -p -m 444 misc/systemd-httpd.conf $RPM_BUILD_ROOT/usr/lib/systemd/system/httpd.service.d/%{name}.conf
mkdir -p $RPM_BUILD_ROOT/var/www/wsgi-scripts/%{name}
install -p -m 555 misc/dispatch.wsgi $RPM_BUILD_ROOT/var/www/wsgi-scripts/%{name}
mkdir -p $RPM_BUILD_ROOT/usr/share/%{name}/pyweb
install -p -m 444 pyweb/* $RPM_BUILD_ROOT/usr/share/%{name}/pyweb
mkdir -p $RPM_BUILD_ROOT/usr/libexec/%{name}
install -p -m 555 libexec/initrepos $RPM_BUILD_ROOT/usr/libexec/%{name}/initrepos
install -p -m 555 libexec/publish $RPM_BUILD_ROOT/usr/libexec/%{name}/publish

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

/usr/libexec/%{name}/initrepos

systemctl daemon-reload

for service in %{name} httpd; do
    if ! systemctl is-enabled --quiet $service; then
        systemctl enable $service
    fi
    if systemctl is-active --quiet $service; then
        if [ $service = %{name} ]; then
            systemctl restart $service
        else
            systemctl reload $service
        fi
    else
        systemctl start $service
    fi
done

%files
%config(noreplace) /etc/%{name}.conf
/etc/init.d/*
/etc/httpd/conf.d/*
/var/www/wsgi-scripts/%{name}
/usr/share/%{name}
/usr/libexec/%{name}
/usr/lib/systemd/system/%{name}.service
/usr/lib/systemd/system/httpd.service.d


%changelog
# - Add automatic repository initialization from config file

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
