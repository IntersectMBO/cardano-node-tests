{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.1.15";
  src = fetchPypi {
    inherit pname version;
    sha256 = "0am6lyxh6czjwwxp9amwpkhsszdj9v28w5lcbvsi5kg8dgzns028";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
