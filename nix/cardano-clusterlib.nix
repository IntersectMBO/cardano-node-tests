{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.3.0rc7";
  src = fetchPypi {
    inherit pname version;
    sha256 = "s3U4S4O9VzFGzn9lPrOJErbguxjUZ6MSmdqkId94vqk=";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
