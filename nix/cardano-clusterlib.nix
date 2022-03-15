{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.2.5";
  src = fetchPypi {
    inherit pname version;
    sha256 = "TnVEWvbhnM3fKklXIeZrURV7mBk0D1wodAW6qIlkPRI=";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
