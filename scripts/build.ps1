# Windows/PowerShell: Docker içinde JDK 21 + Maven ile derleme ve testler.
# Kullanım: .\scripts\build.ps1            (varsayılan: -B -ntp verify)
#           .\scripts\build.ps1 -B -ntp -DskipTests package
$root = Split-Path -Parent $PSScriptRoot
$mvnArgs = if ($args.Count -gt 0) { $args } else { @('-B','-ntp','verify') }
docker run --rm `
  -v "${root}:/src" -w /src `
  -v airlab-m2:/root/.m2 `
  -v /var/run/docker.sock:/var/run/docker.sock `
  -e TESTCONTAINERS_HOST_OVERRIDE=host.docker.internal `
  --add-host host.docker.internal:host-gateway `
  maven:3.9-eclipse-temurin-21 mvn @mvnArgs
exit $LASTEXITCODE
