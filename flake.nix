{
  description = "Activity Launcher";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-parts.url = "github:hercules-ci/flake-parts";
    flake-root.url = "github:srid/flake-root";
    jailed-agents.url = "github:andersonjoseph/jailed-agents";
  };

  outputs = inputs @ {
    nixpkgs,
    flake-parts,
    jailed-agents,
    ...
  }:
    flake-parts.lib.mkFlake {inherit inputs;} {
      systems = ["x86_64-linux" "aarch64-darwin"];
      imports = [
        inputs.flake-root.flakeModule
      ];
      perSystem = {
        config,
        system,
        ...
      }: let
        pkgs = import nixpkgs {inherit system;};

        jlib = jailed-agents.lib.${system};
        combinators = jlib.internals.jail.combinators;

        python = pkgs.python314.withPackages (ps: [
          ps.google-api-python-client
          ps.google-auth
          ps.google-auth-oauthlib
        ]);

        androidPkgs = [
          pkgs.openjdk21_headless
          pkgs.android-tools
        ];

        agentPkgs = androidPkgs ++ [python];
      in {
        devShells.default = pkgs.mkShell {
          name = "dev-shell";
          packages =
            agentPkgs
            ++ [
              (jlib.makeJailedOpencode {
                name = "opencode";
                extraPkgs = agentPkgs;
                extraReadwriteDirs = ["~/.android" "~/.gradle"];
                baseJailOptions = with combinators;
                  jlib.commonJailOptions
                  ++ [
                    (readwrite (noescape "\"$FLAKE_ROOT\""))
                  ];
              })
            ];
          inputsFrom = [config.flake-root.devShell]; # Provides $FLAKE_ROOT in dev shell
          shellHook = ''
            echo "Activity Launcher dev shell"
            echo "Run: python scripts/update-listing.py --help"
          '';
        };

        packages.update-listing = pkgs.writeShellApplication {
          name = "update-listing";
          text = ''
            exec ${python}/bin/python ${./scripts/update-listing.py} "$@"
          '';
        };
      };
    };
}
