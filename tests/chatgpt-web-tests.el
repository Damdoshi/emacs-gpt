;;; chatgpt-web-tests.el --- Behavioral tests -*- lexical-binding: t; -*-
(require 'ert)
(require 'chatgpt-web)

(defconst chatgpt-web-test--snapshot
  '(:url "https://chatgpt.com/c/abcdefgh" :title "Conversation test"
    :messages ((:id "m1" :role "assistant"
                :blocks ((:type "text" :text "Bonjour")
                         (:type "code" :language "bash" :text "printf 'été\\n'\n"))))
    :files nil))

(ert-deftest chatgpt-web-background-snapshot-keeps-selected-buffer ()
  (let ((selected (current-buffer))
        (window (selected-window))
        (target (generate-new-buffer " *chatgpt-test*")))
    (unwind-protect
        (progn
          (with-current-buffer target
            (chatgpt-web-mode)
            (cl-letf (((symbol-function 'chatgpt-web-execute-block) (lambda (&rest _) (ert-fail "Execution automatique")))
                      ((symbol-function 'chatgpt-web-apply-patch-block) (lambda (&rest _) (ert-fail "Patch automatique"))))
              (chatgpt-web--update chatgpt-web-test--snapshot))
            (goto-char (point-min))
            (search-forward "printf")
            (should (equal (plist-get (chatgpt-web--block-at-point) :text) "printf 'été\\n'\n")))
          (should (eq selected (current-buffer)))
          (should (eq window (selected-window))))
      (kill-buffer target))))

(ert-deftest chatgpt-web-split-json-lines-correlate-replies ()
  (let ((process (make-process :name "chatgpt-test-pipe" :command '("cat")
                               :connection-type 'pipe :noquery t))
        (chatgpt-web--pending (make-hash-table :test #'eql))
        (received nil))
    (unwind-protect
        (progn
          (puthash 11 (list :buffer (current-buffer)
                            :callback (lambda (result error-data _buffer)
                                        (should-not error-data) (setq received result)))
                   chatgpt-web--pending)
          (chatgpt-web--filter process "{\"id\":11,\"result\":{\"text\":\"été")
          (should-not received)
          (chatgpt-web--filter process "\"}}\n{\"event\":\"ready\"}\n")
          (should (equal (plist-get received :text) "été"))
          (should (= 0 (hash-table-count chatgpt-web--pending))))
      (delete-process process))))

(ert-deftest chatgpt-web-last-directory-is-shared-between-conversations ()
  (let ((directory (make-temp-file "chatgpt-test-dir-" t))
        (chatgpt-web-working-directory default-directory))
    (unwind-protect
        (progn (chatgpt-web-set-directory directory)
               (with-temp-buffer
                 (chatgpt-web-mode)
                 (should (equal (chatgpt-web--directory nil) (file-name-as-directory directory)))))
      (delete-directory directory))))

(ert-deftest chatgpt-web-send-failure-preserves-draft ()
  (let ((conversation (generate-new-buffer " *conversation-test*"))
        (draft (generate-new-buffer " *draft-test*"))
        (chatgpt-web--pending (make-hash-table :test #'eql))
        callback)
    (unwind-protect
        (progn
          (with-current-buffer conversation (chatgpt-web-mode))
          (with-current-buffer draft
            (text-mode)
            (chatgpt-web-compose-mode 1)
            (setq chatgpt-web--conversation-buffer conversation)
            (insert "Message important")
            (cl-letf (((symbol-function 'chatgpt-web--request)
                       (lambda (_method _params cb &optional _buffer) (setq callback cb))))
              (chatgpt-web-send)))
          (funcall callback nil '(:message "Connexion expirée") conversation)
          (with-current-buffer draft (should (equal (buffer-string) "Message important"))))
      (kill-buffer conversation)
      (with-current-buffer draft (set-buffer-modified-p nil))
      (kill-buffer draft))))

(ert-deftest chatgpt-web-response-does-not-clear-a-newer-draft ()
  (let ((conversation (generate-new-buffer " *conversation-test*"))
        (draft (generate-new-buffer " *draft-test*"))
        (chatgpt-web--pending (make-hash-table :test #'eql))
        callback)
    (unwind-protect
        (progn
          (with-current-buffer conversation (chatgpt-web-mode))
          (with-current-buffer draft
            (text-mode) (chatgpt-web-compose-mode 1)
            (setq chatgpt-web--conversation-buffer conversation)
            (insert "Premier message")
            (cl-letf (((symbol-function 'chatgpt-web--request)
                       (lambda (_method _params cb &optional _buffer) (setq callback cb))))
              (chatgpt-web-send))
            (erase-buffer) (insert "Deuxième message"))
          (funcall callback chatgpt-web-test--snapshot nil conversation)
          (with-current-buffer draft (should (equal (buffer-string) "Deuxième message"))))
      (kill-buffer conversation)
      (with-current-buffer draft (set-buffer-modified-p nil))
      (kill-buffer draft))))

(ert-deftest chatgpt-web-executes-in-selected-directory-without-shell-interpolation ()
  (let* ((directory (make-temp-file "chatgpt dossier " t))
         (chatgpt-web-working-directory (file-name-as-directory directory))
         (block '(:type "code" :language "sh" :text "printf 'réussi' > resultat.txt\n"))
         output)
    (unwind-protect
        (cl-letf (((symbol-function 'display-buffer) (lambda (&rest _) nil)))
          (setq output (chatgpt-web-execute-block block nil))
          (let ((process (get-buffer-process output))
                (deadline (+ (float-time) 5)))
            (while (and (process-live-p process) (< (float-time) deadline))
              (accept-process-output process 0.05))
            (should (= 0 (process-exit-status process))))
          (should (equal (with-temp-buffer
                           (insert-file-contents (expand-file-name "resultat.txt" directory))
                           (buffer-string)) "réussi")))
      (when (buffer-live-p output) (kill-buffer output))
      (delete-directory directory t))))

(ert-deftest chatgpt-web-refuses-patch-with-unsaved-buffer ()
  (let* ((directory (make-temp-file "chatgpt-patch-" t))
         (chatgpt-web-working-directory (file-name-as-directory directory))
         (file (expand-file-name "f.txt" directory))
         (buffer (find-file-noselect file)))
    (unwind-protect
        (progn
          (with-current-buffer buffer (insert "travail non enregistré"))
          (cl-letf (((symbol-function 'chatgpt-web--local-process)
                     (lambda (&rest _) (ert-fail "Patch lancé malgré un tampon modifié"))))
            (should-error
             (chatgpt-web-apply-patch-block '(:type "code" :language "diff" :text "patch") nil)
             :type 'user-error)))
      (with-current-buffer buffer (set-buffer-modified-p nil))
      (kill-buffer buffer)
      (delete-directory directory t))))

(ert-deftest chatgpt-web-refreshes-patched-files-and-preserves-new-edits ()
  (let* ((directory (make-temp-file "chatgpt-refresh-" t))
         (file (expand-file-name "f.txt" directory))
         buffer)
    (unwind-protect
        (progn
          (write-region "ancien" nil file nil 'silent)
          (setq buffer (find-file-noselect file))
          (write-region "nouveau contenu" nil file nil 'silent)
          (chatgpt-web--refresh-files directory)
          (with-current-buffer buffer
            (should (equal (buffer-string) "nouveau contenu"))
            (goto-char (point-max)) (insert " et travail local"))
          (write-region "autre changement disque" nil file nil 'silent)
          (chatgpt-web--refresh-files directory)
          (with-current-buffer buffer
            (should (equal (buffer-string) "nouveau contenu et travail local"))))
      (when (buffer-live-p buffer)
        (with-current-buffer buffer (set-buffer-modified-p nil)) (kill-buffer buffer))
      (delete-directory directory t))))

(provide 'chatgpt-web-tests)
;;; chatgpt-web-tests.el ends here
