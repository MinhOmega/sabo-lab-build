# Sabo Lab Windows build orchestrator

Repo public này chỉ chứa workflow và helper điều phối. Source và release đích vẫn ở **private** `MinhOmega/sabo-lab-next`. Không đổi visibility của source, không upload source, installer hoặc detailed build log vào Actions artifact/cache của repo public.

Pipeline chạy trên runner chuẩn `windows-2022`: tải đúng commit private → kiểm tra assets → frontend build/tests → tests 7 crate và desktop → build NSIS release → upload EXE, SHA256, provenance và log vào release **private**. Log công khai chỉ có trạng thái stage; output của compiler/tests được chuyển vào `build-detail.txt` private. Đây là installer thử nghiệm; build thành công chưa xác nhận Nox/LDPlayer/gameplay, chưa ký Authenticode và chưa tạo artifact updater có chữ ký.

## Cấu hình một lần

1. Tạo **fine-grained personal access token** với resource owner `MinhOmega`, chỉ chọn repository **`sabo-lab-next`**, quyền **Contents: Read and write**. Metadata: Read là quyền đi kèm. Token cần đọc source và tạo/upload release, không cần quyền admin account, workflow write hay toàn bộ repo. Đặt thời hạn và rotate khi hết hạn. Không dán token vào issue/chat/source.
2. Vào repo **`sabo-lab-build` → Settings → Secrets and variables → Actions → New repository secret**. Name: **`SABO_PRIVATE_REPO_TOKEN`**, value là token trên. Có thể thay bằng environment secret cùng tên tại `private-build`.
3. Vào **Settings → Environments → private-build**. Chỉ cho branch `main` deploy; đặt required reviewers để review trước khi dùng token. Người có quyền write workflow có thể truy cập secrets qua code của họ; giới hạn collaborators và bảo vệ `main`. Không approve thay đổi workflow/source SHA chưa review.
4. Tuỳ chọn: bật variable **`SABO_AUTO_BUILD=true`** để kiểm tra `main` private mỗi giờ. Mặc định chưa bật. Lượt đã có `BUILD-SUCCEEDED.json` trong private release sẽ được bỏ qua. Lượt tự động chỉ tạo draft, không tự publish. Thời gian schedule không phải SLA và có thể trễ.

Fine-grained token được giới hạn repo đích; đừng dùng GitHub password, SSH private key hoặc PAT classic cấp toàn bộ account. Không cần cấp token cho máy member tải installer: member được cấp quyền đọc private repo và tải release bằng tài khoản GitHub của họ.

## Bấm build

Vào **Actions → Build Windows from private source → Run workflow**, branch `main`.

- `source_sha`: nên nhập SHA đầy đủ 40 ký tự của commit private đã review. Để trống thì resolve `main` thành một SHA duy nhất trước tải; pipeline không build một ref đang di chuyển.
- `publish_release`: mặc định **false**, upload vào draft private. Bật **true** để publish prerelease private sau khi toàn bộ test/build/upload thành công. Build lỗi luôn chỉ có draft chứa log; không publish thất bại.

Output nằm ở **`https://github.com/MinhOmega/sabo-lab-next/releases`**, tag `windows-ci-<12 ký tự SHA>`. File tải: `SaboLab-<version>-windows-x64-setup.exe`, `SHA256SUMS.txt`, `build-info.json`. `build-detail.txt` chứa log chi tiết private để sửa compile/test. `BUILD-SUCCEEDED.json` chỉ được upload cuối cùng sau thành công. Pipeline không ghi đè release đã publish; nếu đã có draft cùng SHA thì cập nhật draft. Không tạo `latest.json` để tự update khách hàng từ private release.

Member cần Windows x64 và Nox Android7 32-bit/LDPlayer5 Android7 32-bit. Bản source0.3.10 hiện chỉ cho agent Windows game1.5.30/build10530; game1.5.37 mới có mapping ARM64 Mac và bị chặn trên Windows. Đọc `docs/WINDOWS-MEMBER-TEST.md` trong private repo trước khi test gameplay. Không bỏ compatibility guard.

## Quyền riêng và giới hạn

Không chạy workflow build có secret từ pull request, fork, hoặc input shell tự do. Actions ghim SHA, token chỉ có ở bước fetch/upload; bước build bỏ token/secret khỏi environment và không persist credential vào Git config. Không dùng shared/private cache, public artifact hay đăng detailed exceptions của source. Helper kiểm tra private repo đích trước fetch/upload và chặn archive traversal/link cho các file được trích xuất.

Việc chạy **source private đáng tin cậy** trong public runner không phải sandbox chống source độc hại. Source/package build script bị cài mã độc có thể chủ động gọi mạng hoặc ghi kênh GitHub Actions; redirect stdout không ngăn mọi cách exfiltration. Chỉ build SHA/dependency đã review và không cấp secrets tùy ý. Private source dùng trên runner phải tuân thủ quyền của các artifact đi kèm.

Token thiếu/hết hạn/sai quyền: fetch hoặc upload sẽ báo HTTP401/403/404, không in response body hay token. Nếu runner không được cấp vì account policy/billing, repo public không bảo đảm vượt mọi hạn chế account. Nếu upload thất bại, pipeline không chuyển output sang nơi công khai; runner ephemeral được dọn, chạy lại sau khi sửa token/quyền.

Kiểm thử orchestration không truy cập private repo:

```sh
python3 -B -m unittest discover -s tests
```

Tham chiếu: [GitHub standard public runners miễn phí](https://docs.github.com/en/billing/concepts/product-billing/github-actions), [Actions secure use](https://docs.github.com/en/actions/reference/security/secure-use), [release API/Contents permission](https://docs.github.com/en/rest/releases/releases), [release assets](https://docs.github.com/en/rest/releases/assets).
