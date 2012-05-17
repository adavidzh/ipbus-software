#
# spefile for uHAL Library Tests
#
Name: %{name} 
Version: %{version} 
Release: %{release} 
Packager: %{packager}
Summary: uHAL Library Tests
License: BSD License
Group: CACTUS
Source: https://svnweb.cern.ch/trac/cactus/browser/trunk/uhal/tests
URL: https://svnweb.cern.ch/trac/cactus 
BuildRoot: %{_tmppath}/%{name}-%{version}-%{release}-buildroot 
Prefix: %{_prefix}

%description
uHAL Library Tests

%prep

%build


%install 
curdir=`pwd` 

# copy includes to RPM_BUILD_ROOT and set aliases
mkdir -p $RPM_BUILD_ROOT%{_prefix}/bin
cp -rp %{sources_dir}/bin/* $RPM_BUILD_ROOT%{_prefix}/bin/.

#Change access rights
chmod -R 555 $RPM_BUILD_ROOT%{_prefix}/bin

#return to working directory
cd $curdir 


%clean 

%post 

%postun 

%files 
%defattr(-, root, root) 
%{_prefix}/bin/*
