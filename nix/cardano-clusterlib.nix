{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.1.29";
  src = fetchPypi {
    inherit pname version;
    sha256 = "116445hcs3049z1mwgiqs40ljx2swn76pdrcpcgbpcy8mfdn1ii6";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
