/*
 * session_manager.cpp — 多会话管理器的实现
 */

#include "orderbook/session_manager.h"

namespace orderbook {

std::string SessionManager::create_session(const std::string& symbol) {
    // 生成会话 ID
    std::string sid = "ses_" + generate_id();

    // std::make_unique 在堆上创建一个 Session 对象，返回 unique_ptr
    // 堆 (heap) vs 栈 (stack)：
    //   栈：函数结束后自动销毁（局部变量）
    //   堆：手动管理生命周期（通过 new/delete 或智能指针）
    //   unique_ptr 让堆上的对象也能"自动销毁"
    sessions_[sid] = std::make_unique<Session>(sid, symbol);

    return sid;
}

Session* SessionManager::get_session(const std::string& session_id) {
    // find() 在哈希表中查找
    auto it = sessions_.find(session_id);
    if (it != sessions_.end()) {
        // it->second 是 unique_ptr<Session>
        // .get() 从 unique_ptr 中取出裸指针
        // 注意：这里只是"借用"指针，不转移所有权
        // unique_ptr 仍然持有 Session 的所有权
        return it->second.get();
    }
    return nullptr;   // 没找到
}

bool SessionManager::remove_session(const std::string& session_id) {
    // erase 会从 map 中移除元素
    // 因为 value 是 unique_ptr，移除时 unique_ptr 被销毁
    // unique_ptr 销毁时自动 delete 它指向的 Session 对象
    // → 不需要手动 delete，不会内存泄漏！
    return sessions_.erase(session_id) > 0;
    // erase 返回删除的元素数量（0 或 1）
}

std::vector<std::string> SessionManager::list_sessions() const {
    std::vector<std::string> ids;
    // reserve 预分配内存，避免 push_back 时多次扩容
    ids.reserve(sessions_.size());

    for (const auto& [id, session_ptr] : sessions_) {
        ids.push_back(id);
    }
    return ids;
}

size_t SessionManager::session_count() const {
    // size_t 是无符号整数类型，专门用来表示"大小/数量"
    // 它保证是非负的，且足够大（64 位系统上是 8 字节）
    return sessions_.size();
}

}  // namespace orderbook
