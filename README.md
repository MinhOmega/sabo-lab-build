# Sabo Lab desktop build orchestrator

Repo public này chỉ chứa workflow và helper điều phối. Source luôn ở **private** `MinhOmega/sabo-lab-next`. Có hai luồng: Windows test build lưu installer/log private; signed desktop release chủ động phát hành **chỉ binary có chữ ký và feed cập nhật** tại [releases công khai](https://github.com/MinhOmega/sabo-lab-build/releases). Không upload source hoặc detailed build log vào Actions artifact/cache/release công khai.

Pipeline chạy trên runner chuẩn `windows-2022`: tải đúng commit private → kiểm tra assets → frontend build/tests → tests 7 crate và desktop → build NSIS release → upload EXE, SHA256, provenance và log vào release **private**. Log công khai chỉ có trạng thái stage; output của compiler/tests được chuyển vào `build-detail.txt` private. Đây là installer thử nghiệm; build thành công chưa xác nhận Nox/LDPlayer/gameplay, chưa ký Authenticode và chưa tạo artifact updater có chữ ký.

## Bản build đã kiểm chứng

[Run 41206806274](https://github.com/MinhOmega/sabo-lab-build/actions/runs/41206806274) đã thành công trên Windows2022 từ private commit `bc1ed4a1b025bcb3279b8ac1d8d7777d9055c5af`: 257 frontend tests, 186 desktop native tests, 160 tests của 7 crate và NSIS release build. Installer0.3.10 khoảng41MB ở [prerelease private](https://github.com/MinhOmega/sabo-lab-next/releases/tag/windows-ci-bc1ed4a1b025). SHA-256 được đối chiếu sau khi tải. Chưa kiểm chứng install/gameplay Windows hoặc ký Authenticode/updater.

## Phát hành Mac/Windows có chữ ký

Vào **Actions → Signed desktop release from private source → Run workflow** trên `main`:

- `source_sha`: SHA đầy đủ 40 ký tự của private source đã review.
- `tag`: `app-v<version>` khớp version trong source, ví dụ `app-v0.3.11`.

Environment `private-build` chỉ cho `main`. Secrets: `SABO_PRIVATE_REPO_TOKEN` như bên dưới, và `TAURI_SIGNING_PRIVATE_KEY` của nhà phát hành. Không tạo lại khóa ký theo từng lượt build; app pin public key tương ứng. Gói Mac ARM64 và Windows x64 được build với feature `commercial-license`, có màn nhập email/key. Bước build chỉ nhận khóa ký ở công đoạn đóng gói cuối, không nhận GitHub PAT.

Hai nền tảng cùng thành công mới publish release cùng `latest.json`. Nguồn và log compiler giữ private; binary/signature được công khai có chủ đích. Publish dùng `GITHUB_TOKEN` chỉ có Contents:write của repo này, không cần PAT rộng hơn để ghi sang repo khác. Người dùng kiểm tra/tải cập nhật ẩn danh; không nhập GitHub token trong app. Chữ ký updater không thay Windows Authenticode hoặc Apple notarization.

Logs từng nền tảng ở draft **private** `signed-<platform>-ci-<SHA>`. Nếu draft public đã có asset từ lượt thất bại, kiểm tra và xóa asset của đúng lượt lỗi trước khi build lại; helper từ chối ghi đè âm thầm. Release đã publish phải tăng version cho lượt mới.

## Cấu hình một lần

1. Tạo **fine-grained personal access token** với resource owner `MinhOmega`, chỉ chọn repository **`sabo-lab-next`**, quyền **Contents: Read and write**. Metadata: Read là quyền đi kèm. Token cần đọc source và tạo/upload release, không cần quyền admin account, workflow write hay toàn bộ repo. Đặt thời hạn và rotate khi hết hạn. Không dán token vào issue/chat/source.
2. Vào repo **`sabo-lab-build` → Settings → Secrets and variables → Actions → New repository secret**. Name: **`SABO_PRIVATE_REPO_TOKEN`**, value là token trên. Có thể thay bằng environment secret cùng tên tại `private-build`.
3. Vào **Settings → Environments → private-build**. Chỉ cho branch `main` deploy; đặt required reviewers để review trước khi dùng token. Người có quyền write workflow có thể truy cập secrets qua code của họ; giới hạn collaborators và bảo vệ `main`. Không approve thay đổi workflow/source SHA chưa review.
4. Tuỳ chọn: bật variable **`SABO_AUTO_BUILD=true`** để kiểm tra `main` private mỗi giờ. Mặc định chưa bật. Lượt đã có `BUILD-SUCCEEDED.json` trong private release sẽ được bỏ qua. Lượt tự động chỉ tạo draft, không tự publish. Thời gian schedule không phải SLA và có thể trễ.

Với Windows test build private, member cần quyền đọc private repo; với signed release public thì tải binary không cần quyền vào source. Fine-grained token được giới hạn repo đích; đừng dùng GitHub password, SSH private key hoặc PAT classic cấp toàn bộ account. Không cần cấp token cho máy member tải installer: member được cấp quyền đọc private repo và tải release bằng tài khoản GitHub của họ.

## Bấm build

Vào **Actions → Build Windows from private source → Run workflow**, branch `main`.

- `source_sha`: nên nhập SHA đầy đủ 40 ký tự của commit private đã review. Để trống thì resolve `main` thành một SHA duy nhất trước tải; pipeline không build một ref đang di chuyển.
- `publish_release`: mặc định **false**, upload vào draft private. Bật **true** để publish prerelease private sau khi toàn bộ test/build/upload thành công. Build lỗi luôn chỉ có draft chứa log; không publish thất bại.

Output nằm ở **`https://github.com/MinhOmega/sabo-lab-next/releases`**, tag `windows-ci-<12 ký tự SHA>`. File tải: `SaboLab-<version>-windows-x64-setup.exe`, `SHA256SUMS.txt`, `build-info.json`. `build-detail.txt` chứa log chi tiết private để sửa compile/test. `BUILD-SUCCEEDED.json` chỉ được upload cuối cùng sau thành công. Pipeline không ghi đè release đã publish; nếu đã có draft cùng SHA thì cập nhật draft. Không tạo `latest.json` để tự update khách hàng từ private release.

Member cần Windows x64 và Nox Android7 32-bit/LDPlayer5 Android7 32-bit. Bản source0.3.10 hiện chỉ cho agent Windows game1.5.30/build10530; game1.5.37 mới có mapping ARM64 Mac và bị chặn trên Windows. Đọc `docs/WINDOWS-MEMBER-TEST.md` trong private repo trước khi test gameplay. Không bỏ compatibility guard.

## Quyền riêng và giới hạn

Không chạy workflow build có secret từ pull request, fork, hoặc input shell tự do. Actions ghim SHA, token chỉ có ở bước fetch/upload; bước build bỏ token/secret khỏi environment và không persist credential vào Git config. Không dùng cache/artifact công khai cho dữ liệu private hoặc đăng detailed exceptions của source. Cache dependency nếu bật chỉ nằm trong draft của repo private theo chính sách bên dưới. Helper kiểm tra private repo đích trước fetch/upload và chặn archive traversal/link cho các file được trích xuất.

Việc chạy **source private đáng tin cậy** trong public runner không phải sandbox chống source độc hại. Source/package build script bị cài mã độc có thể chủ động gọi mạng hoặc ghi kênh GitHub Actions; redirect stdout không ngăn mọi cách exfiltration. Chỉ build SHA/dependency đã review và không cấp secrets tùy ý. Private source dùng trên runner phải tuân thủ quyền của các artifact đi kèm.

Token thiếu/hết hạn/sai quyền: fetch hoặc upload sẽ báo HTTP401/403/404, không in response body hay token. Nếu runner không được cấp vì account policy/billing, repo public không bảo đảm vượt mọi hạn chế account. Nếu upload thất bại, pipeline không chuyển output sang nơi công khai; runner ephemeral được dọn, chạy lại sau khi sửa token/quyền.

Kiểm thử orchestration không truy cập private repo:

```sh
python3 -B -m unittest discover -s tests
```

Tham chiếu: [GitHub standard public runners miễn phí](https://docs.github.com/en/billing/concepts/product-billing/github-actions), [Actions secure use](https://docs.github.com/en/actions/reference/security/secure-use), [release API/Contents permission](https://docs.github.com/en/rest/releases/releases), [release assets](https://docs.github.com/en/rest/releases/assets).

## Tăng tốc và kiểm chứng pipeline

CI dùng tối đa **3 Cargo jobs trên Windows** (runner 4 CPU/16 GB) và **2 jobs trên Mac ARM64** (3 CPU/7 GB), khác với giới hạn 1 job để bảo vệ máy Mac local. Release giữ tối ưu cấp 3, panic/strip theo source, nhưng đổi sang **Thin LTO và 16 codegen units**. Thin LTO giảm chi phí link; số codegen units lớn hơn có thể thay đổi kích thước hoặc hiệu năng binary nên cần xem kết quả kiểm chứng, không coi thời gian build nhanh hơn là bằng chứng gameplay.

Test CI không tạo PDB/DWARF; thông báo compiler và các log trace trong app vẫn có vị trí source. Mọi kiểm tra bootstrap, desktop, commercial-license, frontend và chữ ký vẫn chạy. `npm ci --ignore-scripts` bỏ các lifecycle hook không cần cho build (gồm optional file watcher); nếu dependency mới thật sự cần hook thì build sẽ lỗi để review, không tự thực thi hook chưa duyệt.

Source/private compiler output **không** được đưa vào Actions cache của repo public. Cache dependency được lưu thành draft trong chính repo private bằng secret Contents đã có:

- Chỉ Cargo registry và output của dependency bên thứ ba. Không lưu source tree, output của local/path package, target riêng của app, installer/bundle, khóa ký hoặc credentials. Tên lib/bin/test tự sinh cũng bị loại. Local code và frontend luôn build lại từ SHA được chọn, kể cả khi mtime của commit mới có trước lần build cũ.
- Cache key gồm OS/target/image, Rust toolchain, dependency lock/config, native manifest, compile flags/profile và đường dẫn build. Đổi version app không làm mất cache dependency; đổi dependency/toolchain/profile thì tạo key mới.
- Manifest/part/archive đều kiểm tra size/hash. Giới hạn 1 GiB nén, 6 GiB giải nén, 512 MiB/file, 200.000 file; chỉ tối đa 2 cache draft mỗi nền tảng. Archive traversal, symlink/hardlink, Windows stream/device name và file local package đều bị chặn trước khi chuyển cache vào build directory.
- Manifest hoàn tất upload cuối cùng. Cache miss, hết hạn hoặc hỏng sẽ build sạch; không bỏ kiểm tra, không dùng binary cũ. Cache hoàn tất là immutable. PAT chỉ gửi tới GitHub API/upload; URL tải asset đã ký không nhận Authorization.

Mỗi stage ghi duration, return code, cache hit/miss và build policy vào `build-info.json` private. Log tổng hợp chỉ do process Python giữ; compiler nhận file tạm riêng. Trên Windows, mỗi stage chạy trong Job Object do helper sở hữu; wrapper chờ stdin đến khi được gán job, rồi mới chạy Cargo. Đóng job khi stage xong/timeout chỉ dừng các process con của stage đó, kể cả compiler server còn giữ working directory; không kill process SDK toàn máy. Có bài test Windows tạo process con giữ cwd rồi xác nhận dọn được thư mục.

Workflow **Verify signed delivery without publication** nhận SHA source đầy đủ, chạy đúng kiểm tra và đóng gói/ký cả hai nền tảng, rồi chỉ gửi diagnostics/cache vào repo private. Nó không có quyền ghi release public, không cập nhật feed; helper cũng từ chối upload public một bundle verification. Chạy hai lần cùng source để đo build sạch và cache hit. Baseline trước tối ưu của [run 41587411761](https://github.com/MinhOmega/sabo-lab-build/actions/runs/41587411761): Windows **30m35**, Mac **14m11**. Kết quả mới phải đo trên CI, không ước lượng thành số đã đạt.

Kết quả kiểm chứng sau tối ưu (verification có ký, không phát hành):

| Nền tảng | Baseline | Build sạch tối ưu | Lượt kế tiếp | Cache |
| --- | ---: | ---: | ---: | --- |
| Windows x64 | 30m35 | 19m49 | 18m52 | Hai lượt đều cache miss; chưa tuyên bố lợi ích Windows cache |
| Mac ARM64 | 14m11 | 13m47 | 5m33 | Lượt kế tiếp cache hit; tải/kiểm tra/giải nén 43 giây |

[Build sạch 41592155681](https://github.com/MinhOmega/sabo-lab-build/actions/runs/41592155681) và [lượt kiểm chứng tiếp theo 41596937727](https://github.com/MinhOmega/sabo-lab-build/actions/runs/41596937727) đều qua toàn bộ kiểm tra, đóng gói và ký. Build sạch Mac gồm 100 giây tạo/lưu cache lần đầu; các stage build mất 670 giây. Mac cache hit giảm tổng thời gian khoảng 61% so với baseline; Windows chưa có cache đã giảm 35–38%. Đây là số đo trên hosted runner ở những lượt cụ thể; tải runner và thay đổi compiler/profile có thể làm thời gian khác đi. Log cả hai nền tảng không còn warning compiler, Node experimental hoặc npm; GitHub vẫn có thể thêm notice về sức chứa ARM64 runner.

Cache Windows ban đầu bị từ chối tại bước đóng gói bởi `CACHE_FILE_CHANGED`. Bài test hardlink tái hiện đúng lỗi: Cargo có thể dùng cùng inode cho các file `.rlib`/`.lib`, còn `tarfile.gettarinfo` đổi bản thứ hai thành link entry. Bản sửa ghi mỗi file regular từ handle đã xác minh thành dữ liệu thường; vẫn từ chối symlink/reparse point, thay đổi file, path sai và mọi link entry khi restore. Giới hạn cache giữ nguyên; cache không có hoặc không hợp lệ vẫn build sạch. Chưa có số đo cache hit Windows thực tế.

Windows cleanup đã được kiểm chứng riêng trong [41593134576](https://github.com/MinhOmega/sabo-lab-build/actions/runs/41593134576): tất cả native tests và bước xóa thư mục private đều thành công, không warning/error annotation. Cache hit Mac vẫn compile lại các package `sabo-*` hiện tại trong cả test/release; app không được lấy từ cache cũ.

Các action Node24 đã ghim SHA, cache tự động của setup-node tắt rõ ràng, runner Ubuntu/Mac dùng version cụ thể để tránh warning đổi nhãn `latest`. Việc nâng action không tắt warning từ compiler; warning source cần sửa đúng nguyên nhân.

Tài liệu đối chiếu: [runner CPU/RAM](https://docs.github.com/en/actions/reference/runners/github-hosted-runners), [cache security](https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching), [Cargo build cache](https://doc.rust-lang.org/cargo/reference/build-cache.html), [Cargo profiles/Thin LTO](https://doc.rust-lang.org/cargo/reference/profiles.html), [checkout Node24](https://github.com/actions/checkout), [setup-node và tắt automatic cache](https://github.com/actions/setup-node).
