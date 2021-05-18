{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.1.21";
  src = fetchPypi {
    inherit pname version;
    sha256 = "1d61n19cy0an3247vzv6k4gmhvdv4sgd2c4k5g2x7akaj7kln9md";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
