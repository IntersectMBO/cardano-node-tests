{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.1.26";
  src = fetchPypi {
    inherit pname version;
    sha256 = "1qh101dxh1mbbri5vfnllzhwibp4gldfnmpamacxx6h70ihansrj";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
