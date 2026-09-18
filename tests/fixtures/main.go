package main

import (
	"net/http"

	"github.com/gin-gonic/gin"
)

type User struct {
	Name string
}

func helloHandler(c *gin.Context) {
	c.JSON(http.StatusOK, gin.H{"hello": "world"})
}

func createUser(c *gin.Context) {}

func main() {
	r := gin.Default()
	r.GET("/api/hello", helloHandler)
	r.POST("/api/users", createUser)
	_ = r.Run(":8080")
}
