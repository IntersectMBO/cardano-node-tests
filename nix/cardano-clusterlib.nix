{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.1.34";
  src = fetchPypi {
    inherit pname version;
    sha256 = "1w4lcc8bbzvqhjbv83a9k38xqvr52gdlszwihwgmf0hlrw7xvwrw";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
