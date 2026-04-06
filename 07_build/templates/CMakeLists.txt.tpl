cmake_minimum_required(VERSION 3.18)
project(stratum CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# ── Chaquopy Python ───────────────────────────────────────────────────────────
# Headers: needed at compile time for nanobind
# libpython: linked dynamically — Chaquopy provides it on device at runtime
#            We link it here so the linker is satisfied, but the actual
#            libpython3.12.so comes from Chaquopy, not bundled in our .so
set(STRATUM_PYTHON_VERSION "{{PYTHON_VERSION}}")
set(STRATUM_PYTHON_INCLUDE "{{PYTHON_INCLUDE}}")
set(STRATUM_PYTHON_LIB_DIR "{{PYTHON_LIB_DIR}}")

# ── nanobind ──────────────────────────────────────────────────────────────────
add_subdirectory("{{NANOBIND_DIR}}" nanobind EXCLUDE_FROM_ALL)

# ── Source files ──────────────────────────────────────────────────────────────
set(STRATUM_SOURCES
{{SOURCE_FILES}}
)

# ── Build as nanobind module ──────────────────────────────────────────────────
# NB_STATIC = nanobind statically linked INTO stratum.so (single .so goal)
# libpython is NOT statically linked — Chaquopy provides it at runtime
nanobind_add_module(_stratum NB_STATIC ${STRATUM_SOURCES})

# ── Include directories ───────────────────────────────────────────────────────
target_include_directories(_stratum PRIVATE
    "{{CORE_INCLUDE_DIR}}"
    "${STRATUM_PYTHON_INCLUDE}"
)

# ── Compile options ───────────────────────────────────────────────────────────
target_compile_options(_stratum PRIVATE
    -O2
    -Wno-unused-parameter
    -Wno-unused-variable
)

# ── Link libraries ────────────────────────────────────────────────────────────
find_library(log-lib log)
find_library(android-lib android)

target_link_libraries(_stratum PRIVATE
    ${log-lib}
    ${android-lib}
)

# Link libpython dynamically from Chaquopy target
# This satisfies the linker for Python C API symbols (Py_Dealloc etc)
# The actual libpython3.12.so is provided by Chaquopy on device — not bundled
target_link_options(_stratum PRIVATE
    "-L${STRATUM_PYTHON_LIB_DIR}"
    "-lpython${STRATUM_PYTHON_VERSION}"
)

# ── Output ────────────────────────────────────────────────────────────────────
set_target_properties(_stratum PROPERTIES
    OUTPUT_NAME "_stratum"
    PREFIX      ""
    SUFFIX      ".so"
    BUILD_RPATH ""
    INSTALL_RPATH ""
    BUILD_WITH_INSTALL_RPATH TRUE
)
