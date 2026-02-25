/*
 * ============================================================
 * session_manager.h — 多会话管理器
 * ============================================================
 *
 * SessionManager 管理多个 Session（多个并行的模拟实验）。
 *
 * 内部用 std::unordered_map 存储所有会话：
 *   key   = session_id (字符串)
 *   value = unique_ptr<Session>
 *
 * 为什么用 unique_ptr？
 *   unique_ptr 是"独占所有权"的智能指针：
 *   - 一个 Session 对象只能有一个"主人"
 *   - 当 unique_ptr 被销毁时，它指向的 Session 也自动销毁
 *   - 不需要手动 delete，避免内存泄漏
 *
 *   相比裸指针 (Session*):
 *   - 裸指针需要手动 delete，忘了就内存泄漏
 *   - unique_ptr 自动管理，更安全
 *
 * 为什么用 unordered_map 而不是 map？
 *   - unordered_map 是哈希表，查找 O(1)
 *   - map 是红黑树，查找 O(log n)
 *   - 这里不需要排序，只需要快速按 ID 查找，所以用哈希表更快
 *
 * ============================================================
 */

#ifndef ORDERBOOK_SESSION_MANAGER_H
#define ORDERBOOK_SESSION_MANAGER_H

#include <string>
#include <unordered_map>   // 哈希表
#include <memory>          // std::unique_ptr, std::make_unique
#include <vector>
#include "orderbook/session.h"

namespace orderbook {

class SessionManager {
public:
    SessionManager() = default;
    // = default 告诉编译器"用自动生成的默认构造函数就行"

    /// 创建一个新会话
    /// 返回 session_id
    std::string create_session(const std::string& symbol);

    /// 获取指定会话的指针
    /// 如果 session_id 不存在，返回 nullptr
    Session* get_session(const std::string& session_id);

    /// 删除一个会话
    bool remove_session(const std::string& session_id);

    /// 获取所有会话的 ID 列表
    std::vector<std::string> list_sessions() const;

    /// 当前有多少个会话
    size_t session_count() const;

private:
    // key = session_id
    // value = unique_ptr<Session>
    //
    // std::make_unique<Session>(...) 创建一个 Session 对象并返回 unique_ptr
    // sessions_["abc123"] = std::make_unique<Session>("abc123", "AAPL");
    std::unordered_map<std::string, std::unique_ptr<Session>> sessions_;
};

}  // namespace orderbook

#endif // ORDERBOOK_SESSION_MANAGER_H
