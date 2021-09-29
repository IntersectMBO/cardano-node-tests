{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.1.45";
  src = fetchPypi {
    inherit pname version;
    sha256 = "063842hjch27313fjk97dh5fpn712ifn3k860gzllp1b3w66mbdp";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
