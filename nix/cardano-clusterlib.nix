{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.1.19";
  src = fetchPypi {
    inherit pname version;
    sha256 = "0qkyd4dmxw398ydih1r5ixi507pr52hzbf2jrqd0233ixjdg64ms";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
