# ============================================================
# CMakeLists.txt — C++ 数据管道构建配置
# ============================================================
#
# 使用方式：
#   mkdir build && cd build
#   cmake ..
#   make -j$(sysctl -n hw.ncpu)
# ============================================================

cmake_minimum_required(VERSION 3.14)
project(data_pipeline VERSION 1.0.0 LANGUAGES CXX)

# ── C++ 标准 ──
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# ── HTTPS 支持（EastMoney 是 HTTPS） ──
add_definitions(-DCPPHTTPLIB_OPENSSL_SUPPORT)

# ── FetchContent：自动下载第三方库 ──
include(FetchContent)

# nlohmann/json — JSON 解析库
FetchContent_Declare(
    json
    GIT_REPOSITORY https://github.com/nlohmann/json.git
    GIT_TAG        v3.11.3
    GIT_SHALLOW    TRUE
)

# cpp-httplib — HTTP 客户端/服务器库
FetchContent_Declare(
    httplib
    GIT_REPOSITORY https://github.com/yhirose/cpp-httplib.git
    GIT_TAG        v0.18.3
    GIT_SHALLOW    TRUE
)

# GoogleTest — 单元测试框架
FetchContent_Declare(
    googletest
    GIT_REPOSITORY https://github.com/google/googletest.git
    GIT_TAG        v1.14.0
    GIT_SHALLOW    TRUE
)

FetchContent_MakeAvailable(json httplib googletest)

# ── 系统依赖 ──
find_package(OpenSSL REQUIRED)
find_package(SQLite3 REQUIRED)

# ── 头文件搜索路径 ──
include_directories(${CMAKE_SOURCE_DIR}/include)

# ── 源文件列表 ──
set(LIB_SOURCES
    src/symbol_mapper.cpp
    src/kline_parser.cpp
    src/http_client.cpp
    src/proxy_provider.cpp
    src/sqlite_storage.cpp
    src/pipeline_task.cpp
    src/orchestrator.cpp
    src/stats_tracker.cpp
    src/server.cpp
)

# ── 主程序：pipeline_server ──
add_executable(pipeline_server
    src/main.cpp
    ${LIB_SOURCES}
)

target_link_libraries(pipeline_server PRIVATE
    nlohmann_json::nlohmann_json
    httplib::httplib
    OpenSSL::SSL
    OpenSSL::Crypto
    SQLite::SQLite3
)

# ── 测试程序 ──
enable_testing()

set(TEST_SOURCES
    tests/test_symbol_mapper.cpp
    tests/test_kline_parser.cpp
    tests/test_blocking_queue.cpp
    tests/test_sqlite_storage.cpp
    tests/test_orchestrator.cpp
)

add_executable(pipeline_tests
    ${TEST_SOURCES}
    ${LIB_SOURCES}
)

target_link_libraries(pipeline_tests PRIVATE
    nlohmann_json::nlohmann_json
    httplib::httplib
    OpenSSL::SSL
    OpenSSL::Crypto
    SQLite::SQLite3
    GTest::gtest_main
)

include(GoogleTest)
gtest_discover_tests(pipeline_tests)
