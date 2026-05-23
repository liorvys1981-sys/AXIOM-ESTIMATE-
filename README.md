# AXIOM-ESTIMATE-
---
title: "DOCUMENTO MAESTRO DE ESPECIFICACIÓN TÉCNICA Y COMERCIAL"
project: "AXIOM ESTIMATE"
author: "Product Architecture"
date: "2026"
version: "1.0.0"
---

# DOCUMENTO MAESTRO DE ESPECIFICACIÓN: AXIOM ESTIMATE

**Objetivo de la Plataforma:** Desarrollar un ecosistema de software disruptivo y altamente autónomo llamado **AXIOM ESTIMATE**. El sistema competirá directamente contra los tres monopolios de la industria (CCC ONE, Mitchell y Audatex), eliminando por completo los flujos manuales de clics e introduciendo un procesamiento asíncrono con **0% de fricción humana**. La plataforma generará estimaciones integrales en tres verticales en un solo flujo unificado: **Mecánica, Body Shop (Colisión) y Pintura**.

---

## 1. FILOSOFÍA DE DISEÑO E INTERFAZ (UI/UX)

* **Enfoque "Mobile-First":** Diseña la plataforma pensando en dispositivos móviles y tablets desde el primer día, facilitando que el operario camine junto al auto con su dispositivo. Evita las interfaces saturadas de tablas infinitas de la competencia.
* **Pantalla de Ingesta (Acción Única):** Un espacio central limpio de arrastrar y soltar (*Drag & Drop*) donde el usuario sube fotos, videos del recorrido alrededor del vehículo o el archivo de diagnóstico OBD-II, junto con la captura del VIN.
* **Panel del Supervisor:** El usuario no escribe el estimado; actúa como un supervisor que aprueba el trabajo de la IA en 30 segundos. Muestra los resultados en 3 columnas limpias (Mecánica, Body Shop, Pintura) con alertas visuales sutiles (ej. amarillo) para los daños ocultos deducidos por la IA.
* **Módulo de Importación ("Caballo de Troya"):** Diseña un importador de un solo clic que use la IA para leer archivos PDF o formatos exportados de CCC ONE o Mitchell, migrando instantáneamente el historial del taller a nuestra base de datos.
* **Modo Offline con Sincronización:** La app móvil debe permitir capturar fotos y videos en patios o zonas sin señal, guardando los datos localmente y subiéndolos al bus de eventos de la nube en cuanto detecte conexión Wi-Fi.

---

## 2. ARQUITECTURA DE DATOS E INTEGRACIÓN DE APIS MAESTRAS

* **Independencia del Usuario:** Los talleres clientes no necesitan tener contratos con CCC ONE, Mitchell o Audatex para usar nuestro sitio web; la plataforma es libre e independiente para ellos.
* **Licenciamiento Corporativo de APIs:** Como empresa dueña de *AXIOM ESTIMATE*, debemos adquirir las licencias y conectar el backend vía API directamente con los dueños originarios de los datos, principalmente **MOTOR Information Systems** (guías de labor oficiales, materiales de pintura y despieces) y clearinghouses de VIN.
* **Compatibilidad Universal (CIECA):** Todo el output de datos debe ser estrictamente compatible con los estándares de la industria **CIECA (formatos JSON y XML)**, permitiendo exportar e importar archivos sin bloqueos comerciales por parte de aseguradoras o talleres.

---

## 3. SISTEMA MULTI-AGENTE AUTÓNOMO (MAS)

Implementar un bus de eventos de alta velocidad (Redis Pub/Sub / RabbitMQ) para coordinar cuatro agentes especializados de forma asíncrona:

1. **Agente de Visión Computacional (CV):** Segmenta imágenes y videos, detecta deformaciones en paneles externos y decide autónomamente entre Reparar (RPR) o Reemplazar (RPL).
2. **Agente de Inferencia Mecánica (El Cerebro):** Utiliza modelos probabilísticos para deducir fallos mecánicos y estructurales internos ocultos (ej. suspensión, radiador, componentes internos) cruzando el vector de fuerza de impacto visual con los códigos del OBD-II.
3. **Agente de Extracción de Labor:** Conecta los daños con la API de MOTOR para calcular las horas de mano de obra exactas y aplicar fórmulas automatizadas de insumos de pintura (*blending*).
4. **Agente Logístico de Compras:** Rastrea y cotiza repuestos en tiempo real con proveedores locales según la geolocalización del taller, armando pre-órdenes de compra automáticas optimizadas por costo y tiempo de entrega.

---

## 4. REGLAS DE NEGOCIO CRÍTICAS Y BLINDAJE LEGAL

* **Principio de Humano en el Bucle (Human-in-the-loop):** Definir legalmente a la IA como un asistente predictivo para mitigar responsabilidades civiles (*liability*). La interfaz UI exigirá que el estimador valide obligatoriamente un *checkbox* de revisión antes de emitir o enviar el PDF de la cotización final.
* **Filtro de Seguridad CAPA:** El agente logístico solo tiene permitido sugerir repuestos del mercado secundario (Aftermarket) si cuentan con la certificación oficial de la **CAPA** (Certified Automotive Parts Association).
* **Privacidad Automatizada (Data Privacy):** Un micro-agente integrado en el pipeline de visión debe detectar y difuminar de forma inmediata rostros de personas y placas de vehículos al momento de la carga de archivos multimedia.
* **Inclusión Automática de Calibración ADAS:** El sistema integrará de forma obligatoria las líneas de mano de obra para la recalibración de sensores, radares y cámaras de asistencia tras un impacto frontal o trasero, garantizando la seguridad del vehículo y protegiendo el ROI del taller.
* **Matriz de Reglas por Estado (Para Expansión de Costa a Costa):** El backend debe parametrizar las leyes según la geolocalización del taller (ej. *Florida Motor Vehicle Repair Act* en nuestro estado base). El sistema debe ajustar automáticamente el formato del PDF, las cláusulas de protección al consumidor, los impuestos locales a las piezas y los límites legales de desviación de costos.
* **EULA y Jurisdicción:** Los términos de uso deben blindar a la plataforma de disputas de talleres externos, fijando la jurisdicción legal exclusivamente en las cortes del Condado de Miami-Dade, Florida.

---

## 5. ESTRATEGIA MONETARIA Y PLAN DE NEGOCIOS

Sabiendo que la competencia (CCC ONE, Mitchell, Audatex) cobra suscripciones mensuales costosas y rígidas a los talleres (entre $300 y $1,500+ USD al mes), se implementará la siguiente disrupción comercial:

* **Modelo de Monetización Principal: Pago por Uso / Transacción (Pay-per-Estimate):** Permitir el registro gratuito de talleres y cobrar una tarifa fija de entre **$15 y $20 dólares por cada estimado exitoso generado por la IA**. Esto elimina la barrera de entrada para talleres pequeños e independientes.
* **Modelo Enterprise para Aseguradoras y Grandes Flotas:** Contratos basados en volumen masivo de reclamos (*claims*) cobrando un aproximado de **$5.00 a $12.00 dólares por transacción**, compitiendo agresivamente con las tarifas de licencias globales de la competencia.

---

## 6. MAPA DE RUTA PARA EL DESARROLLO (MVP MODULAR)

Para construir el sistema de forma eficiente y controlada, el backend y frontend deben estructurarse en tres fases secuenciales:

* **Fase 1 (Ingesta, Core de Datos y Visión):** Desarrollar la API de entrada de archivos multimedia, la estructura base de datos relacional (PostgreSQL/Redis) y el modelo de visión capaz de identificar las partes externas del auto y determinar si requieren reparación o cambio. **Esta es la fase de desarrollo a puerta cerrada.**
* **Fase 2 (Conectividad e Integración con Usuarios):** Conectar las APIs maestras (o simulación/mocking inicial de MOTOR) para transformar el diagnóstico visual en líneas de presupuesto (M, B, P). **Aquí se abre el sitio web a los usuarios beta para aplicar y validar el sistema con casos reales en el taller.**
* **Fase 3 (Autonomía de Compra y Pasarela):** Desplegar los agentes encargados de la interacción externa con proveedores locales para cerrar el ciclo logístico de suministros e integrar el procesamiento de cobros.

---

> **INSTRUCCIÓN DE EJECUCIÓN INMEDIATA PARA EL AGENTE DE IA:** Comienza generando la propuesta de diseño técnico para la Base de Datos (PostgreSQL/Redis) enfocada en la Fase 1, asegurando que la estructura soporte el almacenamiento de metadatos de los agentes, control de colas de mensajes y los archivos multimedia indexados para **AXIOM ESTIMATE**.
