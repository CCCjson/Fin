# ============================================================
# 订单簿模拟器 — CMake 构建配置
# ============================================================
#
# CMake 是 C++ 项目的"构建系统生成器"。
# 你不直接写 Makefile，而是写 CMakeLists.txt，
# CMake 帮你生成对应平台的 Makefile / Xcode / VS 工程。
#
# 构建命令:
#   mkdir build && cd build
#   cmake ..          ← 生成 Makefile
#   make -j4          ← 编译（-j4 表示用 4 个线程并行编译）
#
# ============================================================

# 最低 CMake 版本要求
cmake_minimum_required(VERSION 3.16)

# 项目名称和语言
project(orderbook_simulator LANGUAGES CXX)

# ── C++ 标准 ──
# C++17 是目前主流的 C++ 标准版本
# 它支持 std::optional、结构化绑定、if constexpr 等现代特性
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)    # 必须支持 C++17，不降级

# ── 编译选项 ──
# -Wall  : 开启所有常见警告
# -Wextra: 开启更多警告
# -O2    : 优化级别 2（Release 模式下自动开启）
add_compile_options(-Wall -Wextra)

# ============================================================
# FetchContent — 自动下载第三方库
# ============================================================
#
# FetchContent 是 CMake 内置的包管理器
# 它会在第一次 cmake 时从 GitHub 下载代码，之后用缓存
# 不需要手动安装任何依赖！

include(FetchContent)

# ── nlohmann/json ──
# C++ 最流行的 JSON 库，用法简单：
#   nlohmann::json j = {{"name", "test"}, {"price", 100.0}};
#   std::string s = j.dump();  // 序列化为字符串
FetchContent_Declare(
    json
    GIT_REPOSITORY https://github.com/nlohmann/json.git
    GIT_TAG        v3.11.3
    GIT_SHALLOW    TRUE       # 浅克隆，只拉最新版本，节省时间
)

# ── cpp-httplib ──
# 轻量级 HTTP 服务器/客户端库，只有一个头文件
# 用它可以在 C++ 里快速搭建 REST API
FetchContent_Declare(
    httplib
    GIT_REPOSITORY https://github.com/yhirose/cpp-httplib.git
    GIT_TAG        v0.15.3
    GIT_SHALLOW    TRUE
)

# ── GoogleTest ──
# Google 出品的 C++ 单元测试框架
# 写法类似: TEST(TestSuite, TestName) { EXPECT_EQ(1+1, 2); }
FetchContent_Declare(
    googletest
    GIT_REPOSITORY https://github.com/google/googletest.git
    GIT_TAG        v1.14.0
    GIT_SHALLOW    TRUE
)

# 下载并配置所有依赖（第一次会比较慢）
FetchContent_MakeAvailable(json httplib googletest)

# ============================================================
# 主程序
# ============================================================

# 收集所有源文件
# GLOB_RECURSE 会递归搜索 src/ 目录下所有 .cpp 文件
file(GLOB_RECURSE SOURCES "src/*.cpp")

# 创建可执行文件
add_executable(orderbook_server ${SOURCES})

# 设置头文件搜索路径
# 这样代码里可以写 #include "orderbook/types.h" 而不用写完整路径
target_include_directories(orderbook_server PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/include
)

# 链接第三方库
target_link_libraries(orderbook_server PRIVATE
    nlohmann_json::nlohmann_json    # JSON 库
    httplib::httplib                 # HTTP 服务器库
)

# ============================================================
# 单元测试
# ============================================================

# enable_testing() 告诉 CMake "这个项目有测试"
enable_testing()

# 收集测试文件
file(GLOB TEST_SOURCES "tests/*.cpp")

# 收集除了 main.cpp 之外的源文件（测试不需要 main 函数）
file(GLOB_RECURSE LIB_SOURCES "src/*.cpp")
list(FILTER LIB_SOURCES EXCLUDE REGEX "main\\.cpp$")

# 创建测试可执行文件
add_executable(orderbook_tests ${TEST_SOURCES} ${LIB_SOURCES})

target_include_directories(orderbook_tests PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/include
)

target_link_libraries(orderbook_tests PRIVATE
    nlohmann_json::nlohmann_json
    httplib::httplib
    GTest::gtest_main               # GoogleTest + 自带 main 函数
)

# 注册测试，这样可以用 ctest 命令运行
include(GoogleTest)
gtest_discover_tests(orderbook_tests)
