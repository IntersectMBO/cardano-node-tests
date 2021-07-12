{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.1.27";
  src = fetchPypi {
    inherit pname version;
    sha256 = "1b39kywrhb8i7n7dwckpa6svvg7z4im85rbwrhf7a07ghdia7l61";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
