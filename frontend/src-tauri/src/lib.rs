use std::path::PathBuf;
use std::process::Command;

/// 仓库根目录：默认写死本机路径，可用环境变量 FIN_REPO 覆盖。
/// （本机自用，不追求可移植；换机器改这里或设 FIN_REPO 即可。）
fn repo_root() -> PathBuf {
    std::env::var("FIN_REPO")
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from("/Users/cccjson/Desktop/Fin"))
}

/// 幂等拉起后端 + 3 个 C++ 服务 + vite dev。
/// 脚本内部会逐个探端口，已在跑的直接跳过；服务用 nohup 派生，
/// 因此 app 退出后仍常驻（关窗不停服务）。这里 spawn 不阻塞。
fn spawn_services() {
    let script = repo_root().join("desktop/start-services.sh");
    if !script.exists() {
        log::error!("start-services.sh 不存在: {}", script.display());
        return;
    }
    match Command::new("/bin/bash").arg(&script).spawn() {
        Ok(child) => log::info!("已拉起 start-services.sh (pid={})", child.id()),
        Err(e) => log::error!("拉起 start-services.sh 失败: {e}"),
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .setup(|app| {
            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
            }
            // 开 app 即拉起本地服务（幂等，已在跑则跳过）。
            // 窗口加载的是 tauri build 打进包里的静态产物（tauri.conf.json 的 frontendDist），
            // 没有加载页也没有跳转；后端冷启动这段由前端 App.tsx 的 useBackendReady 轮询挡着。
            spawn_services();
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
