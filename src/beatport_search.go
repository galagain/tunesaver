package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"strings"

	"unspok3n/beatportdl/internal/beatport"
)

const outputPrefix = "MUSICDL_JSON:"

func readSecret(path string) (string, error) {
	content, err := os.ReadFile(path)
	if err != nil {
		return "", err
	}
	value := strings.TrimRight(string(content), "\r\n")
	if value == "" {
		return "", fmt.Errorf("secret vide")
	}
	return value, nil
}

func run() error {
	usernameFile := flag.String("username-file", "/run/secrets/beatport_username", "fichier contenant l'identifiant")
	passwordFile := flag.String("password-file", "/run/secrets/beatport_password", "fichier contenant le mot de passe")
	cacheFile := flag.String("cache-file", "/state/beatportdl/beatportdl-credentials.json", "cache OAuth BeatportDL")
	proxy := flag.String("proxy", "", "proxy HTTP facultatif")
	flag.Parse()

	query := strings.TrimSpace(strings.Join(flag.Args(), " "))
	if query == "" {
		return fmt.Errorf("recherche vide")
	}
	username, err := readSecret(*usernameFile)
	if err != nil {
		return fmt.Errorf("identifiant Beatport indisponible: %w", err)
	}
	password, err := readSecret(*passwordFile)
	if err != nil {
		return fmt.Errorf("mot de passe Beatport indisponible: %w", err)
	}

	auth := beatport.NewAuth(username, password, *cacheFile)
	client := beatport.New(*proxy, auth)
	if err := auth.LoadCache(); err != nil {
		if err := auth.Init(client); err != nil {
			return fmt.Errorf("authentification Beatport: %w", err)
		}
	}
	results, err := client.Search(query)
	if err != nil {
		return fmt.Errorf("recherche Beatport: %w", err)
	}
	payload, err := json.Marshal(results)
	if err != nil {
		return fmt.Errorf("encodage des résultats: %w", err)
	}
	fmt.Printf("%s%s\n", outputPrefix, payload)
	return nil
}

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, "beatport-search:", err)
		os.Exit(1)
	}
}
