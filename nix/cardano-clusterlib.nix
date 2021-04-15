{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.1.10";
  src = fetchPypi {
    inherit pname version;
    sha256 = "1ggvff8ygj2q2p59m31bxpgn5a3y0i1j1b5nzcs5k4annd2si4qs";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
