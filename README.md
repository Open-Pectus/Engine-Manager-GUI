# Open Pectus Engine Manager GUI
Run multiple [Open Pectus](https://github.com/Open-Pectus/Open-Pectus/) engines in a convenient user interface.

| ![image](https://github.com/Open-Pectus/Engine-Manager-GUI/blob/main/screenshot.png?raw=true)| 
|:--:| 
| *Figure 1. Screenshot of Open Pectus Engine Manager GUI.* |

## Getting Started
[Download latest release](https://github.com/Open-Pectus/Engine-Manager-GUI/releases/download/release/Open.Pectus.Engine.Manager.exe) and run `Pectus Engine Manager.exe`.

Documentation is available at [docs.openpectus.org](https://docs.openpectus.org/latest/).

#### Configure Aggregator
Click `File` and select `Aggregator Settings` to bring up a dialog where you can enter the aggregator hostname, port and select if it uses SSL. Click `Verify and Save` to save changes.

#### Open Aggregator
Click `File` and select `Open Aggregator`.

#### Load Unit Operation Definitions
Click `File` and select `Load UOD`. Select the UOD file (ends with .py).

#### Validate Unit Operation Definition
This is only available when the engine status is `Not running`.

Right click on entry in the `Engine List` and select `Validate .. UOD`. Validation output will be visible in the `Engine Output` pane. During validation the engine status is `Validating...`. Once validation is finished the status changes to `Not running`.

#### Run and Stop Engine
This is only available when the engine status is `Not running`.
 
Right click on entry in the `Engine List` and select `Start ..`. Engine output will be visible in the `Engine Output` pane. The engine status is `Running` while the engine is running. The engine can be stopped by right click and selecting `Stop ...`. When fullt stopped the status changes to `Not running`.
#### Remove UOD
This is only available when the engine status is `Not running`.

Right click on entry in the `Engine List` and select `Remove .. from list`.
