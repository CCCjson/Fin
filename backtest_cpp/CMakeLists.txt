# ============================================================
# CMakeLists.txt — C++ 回测系统的构建配置
# ============================================================
#
# 这个文件告诉 CMake 如何编译我们的项目。
# CMake 是一个"构建系统的生成器"——它不直接编译代码，
# 而是生成 Makefile（或 Xcode/VS 项目），再用 make 来编译。
#
# 使用方式：
#   mkdir build && cd build
#   cmake ..
#   make -j$(nproc)
# ============================================================

cmake_minimum_required(VERSION 3.14)       # 要求 CMake 3.14+（FetchContent 需要）
project(backtest_cpp VERSION 1.0.0 LANGUAGES CXX)  # 项目名 + 版本 + 语言

# ── C++ 标准 ──
set(CMAKE_CXX_STANDARD 17)                # 使用 C++17 标准
set(CMAKE_CXX_STANDARD_REQUIRED ON)       # 必须支持 C++17，否则报错

# ── FetchContent：自动下载第三方库 ──
# 好处：不需要手动安装依赖，cmake 会自动从 GitHub 下载
include(FetchContent)

# nlohmann/json — JSON 解析库（header-only，只有头文件，不需要编译）
FetchContent_Declare(
    json
    GIT_REPOSITORY https://github.com/nlohmann/json.git
    GIT_TAG        v3.11.3
    GIT_SHALLOW    TRUE       # 只下载最新版本，不下载全部历史
)

# cpp-httplib — HTTP 服务器库（header-only）
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

# 让 CMake 去下载并准备这些库
FetchContent_MakeAvailable(json httplib googletest)

# ── 头文件搜索路径 ──
# 告诉编译器去哪里找我们的 #include 文件
# include/ 目录放公共头文件，src/ 目录放策略实现的头文件
include_directories(${CMAKE_SOURCE_DIR}/include ${CMAKE_SOURCE_DIR}/src)

# ── 源文件列表 ──
# 列出所有需要编译的 .cpp 文件（不包括 main.cpp 和测试文件）
set(LIB_SOURCES
    src/portfolio.cpp
    src/engine.cpp
    src/metrics.cpp
    src/data_loader.cpp
    src/server.cpp
    src/risk_manager.cpp
    src/strategies/ma_cross_strategy.cpp
    src/strategies/momentum_strategy.cpp
    src/strategies/macd_strategy.cpp
    src/strategies/rsi_strategy.cpp
    src/strategies/kdj_strategy.cpp
    src/strategies/bollinger_strategy.cpp
    src/strategies/combo_strategy.cpp
    src/strategies/pairs_strategy.cpp
    src/strategies/external_signal_strategy.cpp
)

# ── 主程序：backtest_server ──
add_executable(backtest_server
    src/main.cpp           # 入口 main 函数
    ${LIB_SOURCES}         # 所有库代码
)

# 链接第三方库到主程序
target_link_libraries(backtest_server PRIVATE
    nlohmann_json::nlohmann_json    # JSON 库
    httplib::httplib                # HTTP 服务器库
)

# ── 测试程序 ──
enable_testing()   # 启用 CMake 的测试功能

# 所有测试文件
set(TEST_SOURCES
    tests/test_portfolio.cpp
    tests/test_metrics.cpp
    tests/test_engine.cpp
    tests/test_strategies.cpp
    tests/test_external_signal.cpp
)

add_executable(backtest_tests
    ${TEST_SOURCES}
    ${LIB_SOURCES}
)

target_link_libraries(backtest_tests PRIVATE
    nlohmann_json::nlohmann_json
    httplib::httplib
    GTest::gtest_main              # GoogleTest（自带 main 函数）
)

# 注册测试到 CTest（可以用 ctest 命令运行）
include(GoogleTest)
gtest_discover_tests(backtest_tests)
