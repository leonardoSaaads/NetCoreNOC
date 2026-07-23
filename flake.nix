{
  description = "OptiCorr — zero-configuration SNMP trap correlator";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-24.11";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        python = pkgs.python312;
        opticorr = python.pkgs.buildPythonApplication {
          pname = "opticorr";
          version = "0.1.0";
          pyproject = true;
          src = ./.;
          build-system = [ python.pkgs.setuptools ];
          dependencies = with python.pkgs; [
            pysnmp
            aiosqlite
            fastapi
            uvicorn
            pydantic
          ];
          # The full test suite needs the dev extras; run `make qa` in the dev shell.
          doCheck = false;
        };
      in
      {
        packages.default = opticorr;
        apps.default = {
          type = "app";
          program = "${opticorr}/bin/python -m opticorr.main";
        };
        devShells.default = pkgs.mkShell {
          packages = [ python pkgs.gnumake ];
          shellHook = ''
            test -d .venv || ${python}/bin/python -m venv .venv
            .venv/bin/pip install --quiet -e .[dev]
          '';
        };
      });
}
