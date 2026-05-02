DistributedSolverOpen
=====================

Sistema distribuido para resolucion de problemas simples.

El sistema esta formado por:

  * Un servicio de directorio
  * Solver generico que recibe las peticiones y las distribuye
  * Solver para problemas ARITH
  * Solver para problemas MFREQ
  * Un logger de la actividad de los solvers genericos
  * Un cliente que manda peticiones a los solver genericos

El servicio de directorio hace de servicio de descubrimiento y es utilizado por los agentes cada vez que tienen que
asignar una tarea a otros agentes

 * DirectoryService.py

    Mantiene un registro de los agentes en el sistema

    Parametros
      --open = permite conexiones desde hosts remotos (no por defecto)
      --verbose = va escribiendo por terminal las peticiones que recibe el servidor http
      --port = port de comunicacion (9000 por defecto)
      --schedule = Algoritmo para el reparto de carga entre agentes
                  (equaljobs = todos los agentes registrados son asignados el mismo numero de veces,
                   random = los agentes registrados actualmente se asignan al azar
                   random por defecto)

    Entradas Web:
       /info = Registro de agentes

 * Client.py

    Cliente que lanza peticiones a los solver genericos

    Parametros:
      --open = permite conexiones desde hosts remotos (no por defecto)
      --verbose = va escribiendo por terminal las peticiones que recibe el servidor http
      --port = port de comunicacion (9001 por defecto)
      --dir = Direccion completa del servicio de directorio

    Entradas Web:
       /iface = Formulario para enviar problemas
       /info = Lista de problemas enviados

 * Solver.py

    Solver generico que hace de front-end al sistema de resolucion de problemas

    Parametros:
      --open = permite conexiones desde hosts remotos (no por defecto)
      --verbose = va escribiendo por terminal las peticiones que recibe el servidor http
      --port = port de comunicacion (9010 por defecto)
      --dir = Direccion completa del servicio de directorio


    Entradas Web:
       /info = Lista de problemas recibidos

 * Ventas.py

    Agente de gestion de compra de productos

    Parametros:
      --open = permite conexiones desde hosts remotos (no por defecto)
      --verbose = va escribiendo por terminal las peticiones que recibe el servidor http
      --port = port de comunicacion (9020 por defecto)
      --dir = Direccion completa del servicio de directorio

 * CentroLogistico.py

    Agente de centro logistico que simula disponibilidad y compra de productos

    Parametros:
      --open = permite conexiones desde hosts remotos (no por defecto)
      --verbose = va escribiendo por terminal las peticiones que recibe el servidor http
      --port = port de comunicacion (9030 por defecto)
      --dir = Direccion completa del servicio de directorio

 * Cercador.py

    Agente de busqueda de productos por filtros

    Parametros:
      --open = permite conexiones desde hosts remotos (no por defecto)
      --verbose = va escribiendo por terminal las peticiones que recibe el servidor http
      --port = port de comunicacion (9040 por defecto)
      --dir = Direccion completa del servicio de directorio

 * Valorador.py

    Agente que devuelve valoraciones de productos

    Parametros:
      --open = permite conexiones desde hosts remotos (no por defecto)
      --verbose = va escribiendo por terminal las peticiones que recibe el servidor http
      --port = port de comunicacion (9050 por defecto)
      --dir = Direccion completa del servicio de directorio

 * Logger.py

    Registra la actividad de los Solvers genericos

      --open = permite conexiones desde hosts remotos (no por defecto)
      --verbose = va escribiendo por terminal las peticiones que recibe el servidor http
      --port = port de comunicacion (9100 por defecto)
      --dir = Direccion completa del servicio de directorio

    Entradas Web:
       /info = Grafica de la actividad de los solvers

----------------------------

Ejecucion del sistema
=====================

Pasos:

 1- Iniciar un DirectoryService y abrir el navegador en la pagina /info del agente 
    (nombre.de.la.maquina:9000/info)

  $ python3 DirectoryService.py --port 9000

 2- Inicial un Logger y esperar a que se registre y abrir el navegador en la 
    pagina /info del agente

  $ python3 Logger.py --port 9100 --dir http://nombre.de.la.maquina:9000

 3- Iniciar una o mas copias de Ventas y CentroLogistico, y una copia de Cercador y Valorador

  $ python3 Ventas.py --port 9020 --dir http://nombre.de.la.maquina:9000
  $ python3 CentroLogistico.py --port 9030 --dir http://nombre.de.la.maquina:9000
  $ python3 Cercador.py --port 9040 --dir http://nombre.de.la.maquina:9000
  $ python3 Valorador.py --port 9050 --dir http://nombre.de.la.maquina:9000

 4- Inicial Client y abrir en el navegador las paginas /iface y /info

  $ python3 Client.py --port 9001 --dir http://nombre.de.la.maquina:9000

 5- Ejecutar problemas desde la pagina /iface del cliente

Si se va a iniciar el sistema desde varias maquinas se han de ejecutar 
los agentes con el parametro --open

