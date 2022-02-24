{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.2.3";
  src = fetchPypi {
    inherit pname version;
    sha256 = "rR/s9p4KW9Upc1bo7a3Fy/GmI9m6U3LfrYXD+vAO/6U=";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
