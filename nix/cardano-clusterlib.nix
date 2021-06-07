{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.1.24";
  src = fetchPypi {
    inherit pname version;
    sha256 = "0x8256w83bbvz4jdjv0hmxx3gd985s6smlki0p2ljh2nxx3vv6gz";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
