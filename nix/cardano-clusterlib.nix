{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.4.0rc4";
  src = fetchPypi {
    inherit pname version;
    sha256 = "R+46i4XYB57bNqphQ6WBX2ZM+uKLWlX7WTrTvdB9xmg=";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
