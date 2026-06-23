cmake_minimum_required(VERSION 3.15)
project(backtester)

set(CMAKE_CXX_STANDARD 17)

# ---------------------------------------------------------------------------
# Coverage instrumentation (GCC / Clang only, disabled by default)
# Enable with:  cmake -DENABLE_COVERAGE=ON ..
# ---------------------------------------------------------------------------
option(ENABLE_COVERAGE "Instrument binaries for gcov/lcov coverage" OFF)
if(ENABLE_COVERAGE)
    if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
        add_compile_options(-O0 -g --coverage)
        add_link_options(--coverage)
    else()
        message(WARNING "ENABLE_COVERAGE is only supported with GCC or Clang")
    endif()
endif()

# ---------------------------------------------------------------------------
# spdlog — header-only structured logging (fetched at configure time)
# ---------------------------------------------------------------------------
include(FetchContent)

FetchContent_Declare(
    spdlog
    GIT_REPOSITORY https://github.com/gabime/spdlog.git
    GIT_TAG        v1.14.1
)
FetchContent_MakeAvailable(spdlog)

include_directories(include)

file(GLOB_RECURSE SOURCES "src/*.cpp")

# ---------------------------------------------------------------------------
# Production binary (no LibTorch required)
# ---------------------------------------------------------------------------
add_executable(backtest main.cpp ${SOURCES})
target_link_libraries(backtest PRIVATE spdlog::spdlog_header_only)

# ---------------------------------------------------------------------------
# Tests — Google Test fetched at configure time, no manual install required.
# Skipped when BUILD_TESTING=OFF (e.g. in Docker production builds).
# ---------------------------------------------------------------------------
include(CMakeDependentOption)
option(BUILD_TESTING "Build the test suite" ON)

if(BUILD_TESTING)
    include(FetchContent)

    FetchContent_Declare(
        googletest
        URL https://github.com/google/googletest/archive/refs/tags/v1.14.0.zip
        DOWNLOAD_EXTRACT_TIMESTAMP TRUE
    )

    set(gtest_force_shared_crt ON CACHE BOOL "" FORCE)
    FetchContent_MakeAvailable(googletest)

    enable_testing()

    file(GLOB TEST_SOURCES "tests/*.cpp")

    # Test binary: same src/*.cpp (no main.cpp) + GTest's main().
    # MLStrategy.cpp is included but ML_STRATEGY_ENABLED is NOT defined here,
    # so all LibTorch code is compiled out — tests run without LibTorch.
    add_executable(backtester_tests ${TEST_SOURCES} ${SOURCES})
    target_include_directories(backtester_tests PRIVATE include)
    target_link_libraries(backtester_tests GTest::gtest_main spdlog::spdlog_header_only)

    include(GoogleTest)
    gtest_discover_tests(backtester_tests)
endif()

# ---------------------------------------------------------------------------
# ML backtest binary — requires LibTorch (optional).
#
# To enable:
#   cmake -DCMAKE_PREFIX_PATH=/path/to/libtorch ..
#
# Download LibTorch (CPU or CUDA):
#   https://pytorch.org/get-started/locally/
#   Choose: Stable | Your OS | LibTorch | C++ / Java | CPU
# ---------------------------------------------------------------------------
find_package(Torch QUIET)

if(Torch_FOUND)
    message(STATUS "LibTorch ${Torch_VERSION} found — building ml_backtest")

    add_executable(ml_backtest ml_main.cpp ${SOURCES})
    target_compile_definitions(ml_backtest PRIVATE ML_STRATEGY_ENABLED)
    target_include_directories(ml_backtest PRIVATE include)
    target_link_libraries(ml_backtest ${TORCH_LIBRARIES} spdlog::spdlog_header_only)

    # Ensure C++17 ABI matches what LibTorch was built with
    if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
        target_compile_options(ml_backtest PRIVATE -D_GLIBCXX_USE_CXX11_ABI=0)
    endif()

    # Copy LibTorch DLLs into the output directory on Windows
    if(MSVC)
        file(GLOB TORCH_DLLS "${TORCH_INSTALL_PREFIX}/lib/*.dll")
        add_custom_command(TARGET ml_backtest POST_BUILD
            COMMAND ${CMAKE_COMMAND} -E copy_if_different
            ${TORCH_DLLS} $<TARGET_FILE_DIR:ml_backtest>)
    endif()
else()
    message(STATUS "LibTorch not found — ml_backtest target skipped")
    message(STATUS "  To enable: cmake -DCMAKE_PREFIX_PATH=/path/to/libtorch ..")
    message(STATUS "  Download:  https://pytorch.org/get-started/locally/")
endif()
