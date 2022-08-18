{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.3.0rc17";
  src = fetchPypi {
    inherit pname version;
    sha256 = "H2zO45dQB6L3MvpqxvIQtJOnBS4R4WqRz63nYHGut18=";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
