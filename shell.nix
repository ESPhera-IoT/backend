{
  pkgs ? import <nixpkgs> { },
}:
pkgs.mkShell {
  packages = [
    (pkgs.python312.withPackages (
      ps: with ps; [
        pip
      ]
    ))

    pkgs.mosquitto
    pkgs.overmind
  ];
}
