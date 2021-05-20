{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.1.22";
  src = fetchPypi {
    inherit pname version;
    sha256 = "077aypjxblp3knim7sb8z5sv1bl7d0a14vzla3pb4lkrprvakwiy";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
