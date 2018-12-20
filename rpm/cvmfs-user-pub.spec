Summary: CVMFS user publication service
Name: cvmfs-user-pub
Version: 0.1
Release: 1%{?dist}
BuildArch: noarch
Group: Applications/System
License: BSD
BuildRoot: %{_tmppath}/%{name}-%{version}-%{release}-root-%(%{__id_u} -n)
Source0: https://github.com/DrDaveD/%{name}/releases/download/%{version}/%{name}-%{version}.tar.gz

Requires: httpd
Requires: mod_wsgi
Requires: cvmfs-server

%description
Accepts tarballs from authenticated users and publishes them in a
cvmfs repository.

%prep
%setup -q

%install
mkdir -p $RPM_BUILD_ROOT/etc/httpd/conf.d
install -p -m 444 misc/%{name}.conf $RPM_BUILD_ROOT/etc/httpd/conf.d/10-%{name}.conf
mkdir -p $RPM_BUILD_ROOT/var/www/wsgi-scripts/%{name}
install -p -m 555 misc/dispatch.wsgi $RPM_BUILD_ROOT/var/www/wsgi-scripts/%{name}
mkdir -p $RPM_BUILD_ROOT/usr/share/%{name}/pyweb
install -p -m 444 pyweb/* $RPM_BUILD_ROOT/usr/share/%{name}/pyweb

%post
! /sbin/service httpd status >/dev/null || /sbin/service httpd reload

%files
/etc/httpd/conf.d/*
/var/www/wsgi-scripts/%{name}
/usr/share/%{name}


%changelog
* Thu Dec 20 2018 Dave Dykstra <dwd@fnal.gov> 0.1-1
- Initial version
