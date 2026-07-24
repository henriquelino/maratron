cmake_minimum_required(VERSION 3.16)
project(driver_maratron LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# --- OpenVR headers -------------------------------------------------------
# Drivers are header-only against the runtime (no openvr_api.lib link needed).
# Prefer a vendored copy at third_party/openvr/headers; otherwise fetch it.
set(OPENVR_HEADERS "${CMAKE_SOURCE_DIR}/third_party/openvr/headers")
if(NOT EXISTS "${OPENVR_HEADERS}/openvr_driver.h")
    # Fetch ONLY the driver header (no full-repo clone / no openvr_api build).
    file(DOWNLOAD
        "https://raw.githubusercontent.com/ValveSoftware/openvr/v2.5.1/headers/openvr_driver.h"
        "${OPENVR_HEADERS}/openvr_driver.h"
        SHOW_PROGRESS TLS_VERIFY ON)
endif()

add_library(driver_maratron SHARED src/driver_maratron.cpp)
target_include_directories(driver_maratron PRIVATE "${OPENVR_HEADERS}")

# SteamVR requires exactly driver_<manifestname>.dll under bin/win64.
set(OUT_DIR "${CMAKE_SOURCE_DIR}/maratron/bin/win64")
set_target_properties(driver_maratron PROPERTIES
    OUTPUT_NAME "driver_maratron"
    PREFIX ""
    RUNTIME_OUTPUT_DIRECTORY "${OUT_DIR}")
foreach(cfg RELEASE DEBUG RELWITHDEBINFO MINSIZEREL)
    set_target_properties(driver_maratron PROPERTIES RUNTIME_OUTPUT_DIRECTORY_${cfg} "${OUT_DIR}")
endforeach()

if(MSVC)
    target_compile_options(driver_maratron PRIVATE /W3)
endif()
