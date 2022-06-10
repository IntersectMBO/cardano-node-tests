{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.3.0rc2";
  src = fetchPypi {
    inherit pname version;
    sha256 = "ruANy436sByE+d66LjW81PMVwKbmPwHgbLpS9584gAw=";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
