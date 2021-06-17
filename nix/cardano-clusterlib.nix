{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.1.25";
  src = fetchPypi {
    inherit pname version;
    sha256 = "1x922rc9slqvxhs6ahyajqbw51wim6lmvxnvjwsahs4ibyvcnf79";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
