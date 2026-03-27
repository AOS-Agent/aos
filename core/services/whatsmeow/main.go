package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"sync"
	"syscall"
	"time"

	_ "github.com/mattn/go-sqlite3"
	"github.com/mdp/qrterminal/v3"
	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/store/sqlstore"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
	waLog "go.mau.fi/whatsmeow/util/log"
	"google.golang.org/protobuf/proto"
)

// Message represents a received WhatsApp message
type Message struct {
	Timestamp  time.Time `json:"timestamp"`
	Chat       string    `json:"chat"`
	ChatJID    string    `json:"chat_jid"`
	Sender     string    `json:"sender"`
	SenderJID  string    `json:"sender_jid"`
	Text       string    `json:"text"`
	FromMe     bool      `json:"from_me"`
	IsGroup    bool      `json:"is_group"`
	PushName   string    `json:"push_name"`
}

// SendRequest is the JSON body for the /send endpoint
type SendRequest struct {
	To   string `json:"to"`   // phone number like "1234567890" or JID like "1234567890@s.whatsapp.net"
	Text string `json:"text"` // message body
}

var (
	client      *whatsmeow.Client
	messages    []Message
	msgMu       sync.Mutex
	dataDir     string
	lastQRCode  string
	qrMu        sync.Mutex
)

func init() {
	// Store data next to the binary
	exe, _ := os.Executable()
	dataDir = filepath.Dir(exe)
	// Fallback to current directory
	if dataDir == "" {
		dataDir = "."
	}
}

func eventHandler(evt interface{}) {
	switch v := evt.(type) {
	case *events.Message:
		msg := Message{
			Timestamp: v.Info.Timestamp,
			ChatJID:   v.Info.Chat.String(),
			SenderJID: v.Info.Sender.String(),
			FromMe:    v.Info.IsFromMe,
			IsGroup:   v.Info.IsGroup,
			PushName:  v.Info.PushName,
		}

		// Get chat display name
		if v.Info.IsGroup {
			groupInfo, err := client.GetGroupInfo(context.Background(), v.Info.Chat)
			if err == nil {
				msg.Chat = groupInfo.Name
			} else {
				msg.Chat = v.Info.Chat.User
			}
		} else {
			if v.Info.PushName != "" {
				msg.Chat = v.Info.PushName
			} else {
				msg.Chat = v.Info.Sender.User
			}
		}

		// Sender name
		if v.Info.IsFromMe {
			msg.Sender = "Me"
		} else if v.Info.PushName != "" {
			msg.Sender = v.Info.PushName
		} else {
			msg.Sender = v.Info.Sender.User
		}

		// Extract text
		if v.Message.GetConversation() != "" {
			msg.Text = v.Message.GetConversation()
		} else if v.Message.GetExtendedTextMessage() != nil {
			msg.Text = v.Message.GetExtendedTextMessage().GetText()
		} else {
			// Non-text message, skip
			return
		}

		msgMu.Lock()
		messages = append(messages, msg)
		// Keep last 10000 messages in memory
		if len(messages) > 10000 {
			messages = messages[len(messages)-10000:]
		}
		msgMu.Unlock()

		// Also append to daily log file
		go appendToLog(msg)

		log.Printf("[%s] %s: %s", msg.Chat, msg.Sender, truncate(msg.Text, 80))
	}
}

func appendToLog(msg Message) {
	logDir := filepath.Join(dataDir, "logs")
	os.MkdirAll(logDir, 0755)

	date := msg.Timestamp.Format("2006-01-02")
	logFile := filepath.Join(logDir, date+".jsonl")

	f, err := os.OpenFile(logFile, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0644)
	if err != nil {
		log.Printf("Failed to write log: %v", err)
		return
	}
	defer f.Close()

	data, _ := json.Marshal(msg)
	f.Write(data)
	f.Write([]byte("\n"))
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "..."
}

// HTTP API handlers

func handleHealth(w http.ResponseWriter, r *http.Request) {
	connected := client != nil && client.IsConnected()
	status := map[string]interface{}{
		"connected":  connected,
		"messages":   len(messages),
		"timestamp":  time.Now().Format(time.RFC3339),
	}
	if client != nil && client.Store.ID != nil {
		status["phone"] = client.Store.ID.User
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(status)
}

func handleMessages(w http.ResponseWriter, r *http.Request) {
	// Query params: ?days=1&chat=Family
	daysStr := r.URL.Query().Get("days")
	chatFilter := r.URL.Query().Get("chat")

	days := 1
	if daysStr != "" {
		fmt.Sscanf(daysStr, "%d", &days)
	}

	cutoff := time.Now().AddDate(0, 0, -days)

	msgMu.Lock()
	var filtered []Message
	for _, msg := range messages {
		if msg.Timestamp.Before(cutoff) {
			continue
		}
		if chatFilter != "" {
			match := false
			if contains(msg.Chat, chatFilter) || contains(msg.ChatJID, chatFilter) || contains(msg.Sender, chatFilter) {
				match = true
			}
			if !match {
				continue
			}
		}
		filtered = append(filtered, msg)
	}
	msgMu.Unlock()

	// Also load from daily log files if in-memory doesn't cover the range
	if len(filtered) == 0 {
		filtered = loadFromLogs(days, chatFilter)
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(filtered)
}

func loadFromLogs(days int, chatFilter string) []Message {
	logDir := filepath.Join(dataDir, "logs")
	var result []Message

	for d := 0; d < days; d++ {
		date := time.Now().AddDate(0, 0, -d).Format("2006-01-02")
		logFile := filepath.Join(logDir, date+".jsonl")

		data, err := os.ReadFile(logFile)
		if err != nil {
			continue
		}

		for _, line := range splitLines(data) {
			if len(line) == 0 {
				continue
			}
			var msg Message
			if err := json.Unmarshal(line, &msg); err != nil {
				continue
			}
			if chatFilter != "" {
				if !contains(msg.Chat, chatFilter) && !contains(msg.ChatJID, chatFilter) {
					continue
				}
			}
			result = append(result, msg)
		}
	}
	return result
}

func splitLines(data []byte) [][]byte {
	var lines [][]byte
	start := 0
	for i, b := range data {
		if b == '\n' {
			lines = append(lines, data[start:i])
			start = i + 1
		}
	}
	if start < len(data) {
		lines = append(lines, data[start:])
	}
	return lines
}

func contains(s, substr string) bool {
	return len(substr) > 0 && len(s) >= len(substr) &&
		(s == substr || findSubstring(s, substr))
}

func findSubstring(s, sub string) bool {
	for i := 0; i <= len(s)-len(sub); i++ {
		match := true
		for j := 0; j < len(sub); j++ {
			sc := s[i+j]
			uc := sub[j]
			// Case-insensitive
			if sc != uc && sc != uc+32 && sc != uc-32 {
				match = false
				break
			}
		}
		if match {
			return true
		}
	}
	return false
}

func handleSend(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "POST only", http.StatusMethodNotAllowed)
		return
	}

	var req SendRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "invalid JSON: "+err.Error(), http.StatusBadRequest)
		return
	}

	if req.To == "" || req.Text == "" {
		http.Error(w, `{"error": "to and text are required"}`, http.StatusBadRequest)
		return
	}

	// Parse the recipient JID
	jid, err := parseJID(req.To)
	if err != nil {
		http.Error(w, fmt.Sprintf(`{"error": "%s"}`, err.Error()), http.StatusBadRequest)
		return
	}

	// Send the message
	resp, err := client.SendMessage(context.Background(), jid, &waE2E.Message{
		Conversation: proto.String(req.Text),
	})
	if err != nil {
		http.Error(w, fmt.Sprintf(`{"error": "send failed: %s"}`, err.Error()), http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"success":    true,
		"message_id": resp.ID,
		"timestamp":  resp.Timestamp.Format(time.RFC3339),
	})
}

func parseJID(input string) (types.JID, error) {
	// If it already looks like a JID
	if jid, ok := parseRawJID(input); ok {
		return jid, nil
	}
	// Otherwise treat as phone number
	// Strip +, spaces, dashes
	clean := ""
	for _, c := range input {
		if c >= '0' && c <= '9' {
			clean += string(c)
		}
	}
	if clean == "" {
		return types.JID{}, fmt.Errorf("invalid phone number: %s", input)
	}
	return types.NewJID(clean, types.DefaultUserServer), nil
}

func parseRawJID(input string) (types.JID, bool) {
	// Check if it contains @ (already a JID)
	for _, c := range input {
		if c == '@' {
			jid, err := types.ParseJID(input)
			if err == nil {
				return jid, true
			}
			return types.JID{}, false
		}
	}
	return types.JID{}, false
}

func handleChats(w http.ResponseWriter, r *http.Request) {
	// Return unique chats from messages
	msgMu.Lock()
	chatMap := make(map[string]string) // jid -> name
	for _, msg := range messages {
		if _, ok := chatMap[msg.ChatJID]; !ok {
			chatMap[msg.ChatJID] = msg.Chat
		}
	}
	msgMu.Unlock()

	type ChatInfo struct {
		JID  string `json:"jid"`
		Name string `json:"name"`
	}

	var chats []ChatInfo
	for jid, name := range chatMap {
		chats = append(chats, ChatInfo{JID: jid, Name: name})
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(chats)
}

func handlePair(w http.ResponseWriter, r *http.Request) {
	phone := r.URL.Query().Get("phone")
	if phone == "" {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(400)
		json.NewEncoder(w).Encode(map[string]string{"error": "Missing ?phone= parameter (e.g. ?phone=16475551234)"})
		return
	}

	// Add + prefix if not present
	if phone[0] != '+' {
		phone = "+" + phone
	}

	code, err := client.PairPhone(context.Background(), phone, true, whatsmeow.PairClientChrome, "AOS Bridge")
	if err != nil {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(500)
		json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{
		"code":         code,
		"instructions": "Open WhatsApp on your phone → Settings → Linked Devices → Link a Device → Link with phone number instead → Enter this code",
	})
}

func handleQR(w http.ResponseWriter, r *http.Request) {
	qrMu.Lock()
	code := lastQRCode
	qrMu.Unlock()

	if code == "" {
		// Try reading from file
		data, err := os.ReadFile(filepath.Join(dataDir, "qr.txt"))
		if err == nil && len(data) > 0 {
			code = string(data)
		}
	}

	if code == "" {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(404)
		json.NewEncoder(w).Encode(map[string]string{
			"error": "No QR code available. Bridge may already be paired or QR has expired.",
		})
		return
	}

	// Return as an HTML page with a rendered QR code using a JS library
	w.Header().Set("Content-Type", "text/html")
	fmt.Fprintf(w, `<!DOCTYPE html>
<html><head><title>WhatsApp QR</title>
<script src="https://cdn.jsdelivr.net/npm/qrcode@1.5.4/build/qrcode.min.js"></script>
<style>body{background:#111;display:flex;flex-direction:column;align-items:center;justify-content:center;height:100vh;margin:0;font-family:sans-serif;color:#fff}
h2{margin-bottom:20px}canvas{border-radius:12px}</style>
</head><body>
<h2>Scan with WhatsApp</h2>
<canvas id="qr"></canvas>
<p style="color:#888;margin-top:16px;font-size:14px">WhatsApp → Settings → Linked Devices → Link a Device</p>
<script>QRCode.toCanvas(document.getElementById('qr'),%q,{width:400,margin:2,color:{dark:'#000',light:'#fff'}});</script>
</body></html>`, code)
}

func main() {
	// Database for WhatsApp session
	dbPath := filepath.Join(dataDir, "whatsmeow.db")
	dbLog := waLog.Noop

	container, err := sqlstore.New(context.Background(), "sqlite3", "file:"+dbPath+"?_foreign_keys=on", dbLog)
	if err != nil {
		log.Fatalf("Failed to create store: %v", err)
	}

	deviceStore, err := container.GetFirstDevice(context.Background())
	if err != nil {
		log.Fatalf("Failed to get device: %v", err)
	}

	client = whatsmeow.NewClient(deviceStore, waLog.Noop)
	client.AddEventHandler(eventHandler)

	// Load today's messages from log file into memory
	todayMsgs := loadFromLogs(1, "")
	if len(todayMsgs) > 0 {
		msgMu.Lock()
		messages = append(todayMsgs, messages...)
		msgMu.Unlock()
		log.Printf("Loaded %d messages from today's log", len(todayMsgs))
	}

	// Start HTTP API first so /qr endpoint is available during pairing
	port := os.Getenv("WHATSMEOW_PORT")
	if port == "" {
		port = "7601"
	}

	mux := http.NewServeMux()
	mux.HandleFunc("/health", handleHealth)
	mux.HandleFunc("/messages", handleMessages)
	mux.HandleFunc("/send", handleSend)
	mux.HandleFunc("/chats", handleChats)
	mux.HandleFunc("/qr", handleQR)
	mux.HandleFunc("/pair", handlePair)

	server := &http.Server{
		Addr:    "127.0.0.1:" + port,
		Handler: mux,
	}

	go func() {
		log.Printf("WhatsApp bridge API listening on http://127.0.0.1:%s", port)
		if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Fatalf("HTTP server failed: %v", err)
		}
	}()

	// Connect to WhatsApp
	if client.Store.ID == nil {
		// Need to pair — show QR code, available at http://127.0.0.1:7601/qr
		qrChan, _ := client.GetQRChannel(context.Background())
		err = client.Connect()
		if err != nil {
			log.Fatalf("Failed to connect: %v", err)
		}
		log.Printf("QR code available at http://127.0.0.1:%s/qr", port)

		for evt := range qrChan {
			if evt.Event == "code" {
				qrMu.Lock()
				lastQRCode = evt.Code
				qrMu.Unlock()
				os.WriteFile(filepath.Join(dataDir, "qr.txt"), []byte(evt.Code), 0644)
				fmt.Println("\n=== Scan this QR code with WhatsApp ===")
				fmt.Printf("Or open: http://127.0.0.1:%s/qr\n", port)
				qrterminal.GenerateHalfBlock(evt.Code, qrterminal.L, os.Stdout)
				fmt.Println("=======================================\n")
			} else {
				log.Printf("QR event: %s", evt.Event)
			}
		}
	} else {
		err = client.Connect()
		if err != nil {
			log.Fatalf("Failed to connect: %v", err)
		}
		log.Printf("Connected as %s", client.Store.ID.User)
	}

	// Wait for shutdown
	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, syscall.SIGINT, syscall.SIGTERM)
	<-sigChan

	log.Println("Shutting down...")
	client.Disconnect()
	server.Shutdown(context.Background())
}
