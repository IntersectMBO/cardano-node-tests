{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.1.43";
  src = fetchPypi {
    inherit pname version;
    sha256 = "0x105hfh5pj8krahsg42zjn52qgp2ldcigvmw49bvd3b5afnlv5p";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
