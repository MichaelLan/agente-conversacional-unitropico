# Prerrequisito local ausente

Causa comprobada: `preflight()` sin credenciales produjo `FileNotFoundError`
con `filename == 'docker'`; `command -v docker` no produjo salida.
La nueva categoría controlada es `docker_unavailable`; otros errores de E/S
conservan `local_io_error`. No se imprime el mensaje de excepción.

Los cinco inputs fijos existen y no son symlinks. La comparación local de
hashes/metadatos de 70 archivos (artefactos y fuentes) no detectó cambios;
`execution/` no existía antes ni después. El fallo precede a validar el
preflight, crear lote/reserva/ledger y llamar HTTP: **cero POST y cero consumo
de esta ejecución**, no una afirmación sobre saldo ni consumo histórico.

Verificar con `command -v docker`; solo el usuario, en un entorno donde exista,
puede comprobar `docker info`. No se instala ni repara automáticamente.
No reenviar ni reintentar mientras falte la dependencia; este diagnóstico no
autoriza ningún envío. `remaining_send.py --check` es offline, sin credenciales,
gasto ni reserva; seguirá fallando mientras Docker no esté disponible.
