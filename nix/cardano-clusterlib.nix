{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.1.35";
  src = fetchPypi {
    inherit pname version;
    sha256 = "0c40mg14ds1mps4yhn1a43j4mvpb0hply83pfx4wgy8jyhzc5cr0";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
