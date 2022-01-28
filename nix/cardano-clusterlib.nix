{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.1.51";
  src = fetchPypi {
    inherit pname version;
    sha256 = "d5+jfA4HzhUv6ayCham7cr0SeQJ8GrUvvgHH7kMsJXM=";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
