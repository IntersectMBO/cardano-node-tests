{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.3.0rc5";
  src = fetchPypi {
    inherit pname version;
    sha256 = "6RN/OvapNejKCl5y5h5Gr57g3Xkq2PI1IC0hLNNnZw8=";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
