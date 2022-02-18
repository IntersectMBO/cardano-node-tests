{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.2.1";
  src = fetchPypi {
    inherit pname version;
    sha256 = "XxviwC50KCbuWXpW4GuAwhSTQg80Ve8Jf+JZ9i3N7DU=";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
