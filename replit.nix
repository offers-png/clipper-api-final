{ pkgs }: {
  deps = [
    pkgs.python310Full
    pkgs.python310Packages.pip
    pkgs.python310Packages.uvicorn
    pkgs.python310Packages.fastapi
  ];
}
